"""VX2.5 — Site exit safety service (pure tests).

These tests don't need an app fixture for the pure path of the
safety service — they assemble the inputs (policy/exit_node/
targets/admin) directly. The few cases that hit overview need
an app context because build_overview reads from the DB.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_vx2_5_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                        os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_router_with_backup(app, *, nas_id, backup_age_sec=60):
    """Seed a router + a recent backup so build_overview returns
    backup_status='fresh'."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import router_backups_repo
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret,
                     vendor, nas_type, enabled, created_at,
                     connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik',
                           'hotspot', 1, ?, 'direct')""",
                (nas_id, f"vx25-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )
        router_backups_repo.record(
            tenant_id=1, router_id=nas_id,
            backup_type="binary",
            filename="recent.backup",
            status="success",
        )


def _admin(*perms, super_admin=False):
    """Build a minimal admin shape that admin_permissions
    accepts. The platform's permission resolver looks at
    `is_super_admin` and falls back to an admins_service
    lookup — to keep tests pure we monkeypatch the service
    fallback through a global super_admin=True. For perm
    sub-sets we use the admin's role permissions field via
    a SimpleNamespace + monkeypatched admins service."""
    return SimpleNamespace(
        id=1, username="t", role_id=None,
        is_super_admin=bool(super_admin),
        _vx2_perms=tuple(perms),
    )


@pytest.fixture(autouse=True)
def _stub_admins_service(app, monkeypatch):
    """Replace admins_service.permissions_of with a stub that
    returns the test admin's pre-baked perm tuple.

    Depends on `app` so the monkeypatch happens AFTER the app
    fixture has cleared sys.modules and re-imported everything.
    Otherwise the stub gets blown away when the next test's
    `app` fixture resets the module table."""
    from app.radius.services import admins as adm

    class _FakeStore:
        def get_admin(self, _aid): return None
        def by_id(self, _aid): return None

    class _FakeSvc:
        _store = _FakeStore()
        def permissions_of(self, admin):
            return tuple(getattr(admin, "_vx2_perms", ()) or ())

    monkeypatch.setattr(adm, "get_admins_service",
                         lambda: _FakeSvc())
    yield


def _node(**overrides):
    base = {
        "id": 1, "name": "vps",
        "public_ip": "203.0.113.5",
        "wireguard_interface_name": "wg-vps",
        "wireguard_gateway_ip": "10.10.0.1",
        "enabled": 1,
        "last_health_status": "ok",
    }
    base.update(overrides)
    return base


def _policy(**overrides):
    base = {
        "id": 42, "router_id": 1, "exit_node_id": 1,
        "name": "p", "slug": "p",
        "fail_mode": "block_when_vps_down",
        "include_subdomains": 0,
        "include_router_output": 0, "enabled": 1,
    }
    base.update(overrides)
    return base


def _target(**overrides):
    base = {
        "id": 1, "value": "speedtest.net",
        "normalized_value": "speedtest.net",
        "target_type": "domain",
        "group_name": "speedtest_measurement",
        "include_www": 1, "include_subdomains": 0,
        "status": "active",
    }
    base.update(overrides)
    return base


# ─── Permission gates ───────────────────────────────────────


def test_safety_blocks_when_admin_lacks_view(app):
    _seed_router_with_backup(app, nas_id=1)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=1,
            admin=_admin(),  # no perms at all
            policy=_policy(), exit_node=_node(),
            targets=[_target()],
        )
    assert result.allowed is False
    assert any("view" in r for r in result.blocking_reasons)


def test_safety_blocks_when_admin_lacks_apply(app):
    _seed_router_with_backup(app, nas_id=2)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=2,
            admin=_admin("site_exit.view",
                          "site_exit.preview"),
            policy=_policy(router_id=2),
            exit_node=_node(),
            targets=[_target()],
        )
    assert result.allowed is False
    assert any("site_exit.apply" in r
                for r in result.blocking_reasons)


def test_safety_super_admin_gets_all_perms(app):
    _seed_router_with_backup(app, nas_id=3)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=3,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=3),
            exit_node=_node(),
            targets=[_target()],
            wan_interface_list="WAN",
        )
    # super_admin holds every perm → allowed, given fresh
    # backup + healthy plan.
    assert result.allowed is True
    # FastTrack advisory is always present.
    assert "FastTrack" in result.fasttrack_warning


# ─── Router / VPS sanity ────────────────────────────────────


def test_safety_blocks_when_router_not_found(app):
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=9999,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=9999),
            exit_node=_node(),
            targets=[_target()],
        )
    assert not result.allowed
    assert any("not found" in r for r in result.blocking_reasons)


def test_safety_blocks_when_vps_disabled(app):
    _seed_router_with_backup(app, nas_id=4)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=4,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=4),
            exit_node=_node(enabled=0),
            targets=[_target()],
        )
    assert not result.allowed
    assert any("disabled" in r for r in result.blocking_reasons)


def test_safety_blocks_when_vps_missing(app):
    _seed_router_with_backup(app, nas_id=5)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=5,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=5),
            exit_node=None,
            targets=[_target()],
        )
    assert not result.allowed
    assert any("missing" in r for r in result.blocking_reasons)


# ─── Backup awareness ───────────────────────────────────────


def test_safety_blocks_when_backup_missing_no_override(app):
    """Router exists but has no backup at all → block apply
    unless override permission is granted AND acknowledged."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret,
                     vendor, nas_type, enabled, created_at,
                     connection_mode)
                   VALUES (6, 1, 'r6', '203.0.113.6', 'sek',
                           'mikrotik', 'hotspot', 1, ?,
                           'direct')""",
                (now,),
            )
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=6,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=6),
            exit_node=_node(),
            targets=[_target()],
        )
    # super_admin DOES hold override permission, but
    # `backup_override_acknowledged=False` by default → blocked.
    assert not result.allowed
    assert any("backup" in r.lower()
                for r in result.blocking_reasons)


def test_safety_allows_when_backup_missing_with_acknowledged_override(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret,
                     vendor, nas_type, enabled, created_at,
                     connection_mode)
                   VALUES (7, 1, 'r7', '203.0.113.7', 'sek',
                           'mikrotik', 'hotspot', 1, ?,
                           'direct')""",
                (now,),
            )
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=7,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=7),
            exit_node=_node(),
            targets=[_target()],
            backup_override_acknowledged=True,
        )
    assert result.allowed is True
    # The override path leaves a warning trail.
    assert any("override" in w.lower() for w in result.warnings)


# ─── Risky-group permission ─────────────────────────────────


def test_risky_target_blocks_without_enable_risky_groups_perm(app):
    _seed_router_with_backup(app, nas_id=8)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        # Build an admin that has apply + view + preview but
        # not enable_risky_groups.
        admin = _admin(
            "site_exit.view", "site_exit.preview",
            "site_exit.apply", "site_exit.manage",
        )
        result = evaluate(
            tenant_id=1, nas_id=8,
            admin=admin,
            policy=_policy(router_id=8),
            exit_node=_node(),
            targets=[
                _target(),  # safe group
                _target(id=2, value="expressvpn.com",
                         normalized_value="expressvpn.com",
                         group_name="vpn_provider_pages"),
            ],
        )
    assert not result.allowed
    assert any("risky" in r.lower() or "enable_risky"
                in r for r in result.blocking_reasons)


def test_risky_target_allowed_with_risky_groups_perm(app):
    _seed_router_with_backup(app, nas_id=10)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        admin = _admin(
            "site_exit.view", "site_exit.preview",
            "site_exit.apply", "site_exit.manage",
            "site_exit.enable_risky_groups",
        )
        result = evaluate(
            tenant_id=1, nas_id=10,
            admin=admin,
            policy=_policy(router_id=10),
            exit_node=_node(),
            targets=[
                _target(),
                _target(id=2, value="expressvpn.com",
                         normalized_value="expressvpn.com",
                         group_name="vpn_provider_pages"),
            ],
            wan_interface_list="WAN",
        )
    assert result.allowed is True


# ─── FastTrack + warnings + outputs ─────────────────────────


def test_fasttrack_warning_always_present_when_plan_can_apply(app):
    _seed_router_with_backup(app, nas_id=11)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=11,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=11),
            exit_node=_node(), targets=[_target()],
            wan_interface_list="WAN",
        )
    assert "FastTrack" in result.fasttrack_warning
    # FastTrack also surfaces in warnings list (the source).
    assert any("FastTrack" in w for w in result.warnings)


def test_safety_returns_script_hash_when_can_apply(app):
    _seed_router_with_backup(app, nas_id=12)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=12,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=12),
            exit_node=_node(), targets=[_target()],
            wan_interface_list="WAN",
        )
    assert result.script_hash
    assert len(result.script_hash) == 64   # sha256 hex


def test_safety_to_dict_has_no_secrets(app):
    """The dict the audit layer stores must not contain any
    secret-looking keys/values from the input."""
    _seed_router_with_backup(app, nas_id=13)
    with app.app_context():
        from app.radius.services.site_exit_safety import evaluate
        result = evaluate(
            tenant_id=1, nas_id=13,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=13),
            exit_node=_node(),
            targets=[_target()],
            wan_interface_list="WAN",
        )
        as_dict = result.to_dict()
    serialised = repr(as_dict).lower()
    for tripwire in (
        "private_key", "privatekey", "private-key",
        "secret", "password",
    ):
        assert tripwire not in serialised, (
            f"safety dict contains tripwire {tripwire!r}: "
            f"{as_dict}"
        )


def test_safety_required_confirmations_listed(app):
    _seed_router_with_backup(app, nas_id=14)
    with app.app_context():
        from app.radius.services.site_exit_safety import (
            evaluate, REQUIRED_CONFIRMATIONS,
        )
        result = evaluate(
            tenant_id=1, nas_id=14,
            admin=_admin(super_admin=True),
            policy=_policy(router_id=14),
            exit_node=_node(), targets=[_target()],
            wan_interface_list="WAN",
        )
    # All 5 confirmations must be in the result.
    assert set(result.required_confirmations) \
        == set(REQUIRED_CONFIRMATIONS)
    assert len(REQUIRED_CONFIRMATIONS) == 5
