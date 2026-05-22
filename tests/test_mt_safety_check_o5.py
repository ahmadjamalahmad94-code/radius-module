"""O5 — Pre-execution safety check service."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o5_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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


def _seed_nas(app, *, nas_id, enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, 'direct')""",
                (nas_id, f"o5-rtr-{nas_id}",
                 f"203.0.113.{nas_id}",
                 1 if enabled else 0, now),
            )


# ─── Permission gate ─────────────────────────────────────────


def test_no_admin_is_blocked(app):
    _seed_nas(app, nas_id=1)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=1, admin=None)
    assert v.allowed is False
    assert v.severity == "blocked"
    assert any("صلاحية" in r for r in v.blocking_reasons)


def test_admin_without_perm_is_blocked(app):
    _seed_nas(app, nas_id=2)
    admin = SimpleNamespace(id=99, is_super_admin=False)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=2, admin=admin)
    assert v.allowed is False
    assert v.severity == "blocked"


def test_super_admin_passes_perm_gate(app):
    _seed_nas(app, nas_id=3)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=3, admin=admin)
    # No alerts / no snapshot / missing backup → still allowed
    # but at warning severity (missing backup).
    assert v.allowed is True
    assert v.severity in {"info", "warning"}


# ─── Router state gates ──────────────────────────────────────


def test_unknown_router_blocked(app):
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=9999, admin=admin)
    assert v.allowed is False
    assert v.summary["reason"] == "scope_or_404"


def test_disabled_router_blocked(app):
    _seed_nas(app, nas_id=4, enabled=False)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=4, admin=admin)
    assert v.allowed is False
    assert v.summary["reason"] == "router_disabled"


def test_offline_router_blocked(app):
    _seed_nas(app, nas_id=5)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        # Mark snapshot as failed → health=offline.
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_failure(tenant_id=1, router_id=5,
                       error="connect refused")
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=5, admin=admin)
    assert v.allowed is False
    assert v.summary["reason"] == "router_offline"


# ─── Severity escalation ─────────────────────────────────────


def test_critical_alert_requires_override_admin(app):
    _seed_nas(app, nas_id=6)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o5:6",
            router_id=6, title_ar="crit", severity="critical",
        )
        from app.radius.services.mt_safety_check import evaluate
        # Without override → blocked.
        v = evaluate(tenant_id=1, nas_id=6, admin=admin,
                     override_admin=False)
    assert v.allowed is False
    assert v.severity == "blocked"
    assert v.requires_confirmation is True
    assert v.summary["reason"] == "critical_no_override"


def test_critical_alert_passes_with_override_by_super_admin(app):
    _seed_nas(app, nas_id=7)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o5:7",
            router_id=7, title_ar="crit", severity="critical",
        )
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=7, admin=admin,
                     override_admin=True)
    assert v.allowed is True
    assert v.severity == "critical"
    assert v.requires_confirmation is True
    assert v.summary["override_used"] is True


def test_partial_apply_router_state_is_critical(app):
    _seed_nas(app, nas_id=8)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(
            tenant_id=1, actor="op", action="mt.programming.apply",
            target_type="mikrotik_nas", target_id="8",
            router_id=8, severity="warning",
            result_status="partial",
        )
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=8, admin=admin,
                     override_admin=False)
    # Partial state → critical → blocked without override.
    assert v.allowed is False
    assert v.severity == "blocked"


def test_missing_backup_only_is_warning_not_block(app):
    _seed_nas(app, nas_id=9)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=9, admin=admin)
    assert v.allowed is True
    assert v.severity == "warning"
    assert v.requires_confirmation is True
    assert any("نسخة احتياطية" in w for w in v.warnings)


def test_stale_snapshot_only_is_warning(app):
    _seed_nas(app, nas_id=10)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        # Save success then backdate it to 2h ago.
        from datetime import timedelta, timezone
        from app.radius.db.connection import transaction
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(tenant_id=1, router_id=10, counters={})
        old = (datetime.now(timezone.utc) -
               timedelta(hours=2)).isoformat()
        with transaction() as c:
            c.execute(
                "UPDATE router_snapshots SET last_success_at=? "
                "WHERE router_id=?", (old, 10),
            )
        # Also add a backup so the verdict isn't dominated by
        # missing-backup.
        from app.radius.db.repos import router_backups_repo as br
        br.record(tenant_id=1, router_id=10,
                   backup_type="binary", filename="x",
                   status="success")
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=10, admin=admin)
    assert v.allowed is True
    assert v.severity == "warning"


# ─── Summary shape ───────────────────────────────────────────


def test_to_dict_contains_audit_fields(app):
    _seed_nas(app, nas_id=11)
    admin = SimpleNamespace(id=1, is_super_admin=True)
    with app.app_context():
        from app.radius.services.mt_safety_check import evaluate
        v = evaluate(tenant_id=1, nas_id=11, admin=admin,
                     operation="mt.programming.hotspot.apply")
    d = v.to_dict()
    for k in ("allowed", "severity", "blocking_reasons",
              "warnings", "recommendations",
              "requires_confirmation", "summary"):
        assert k in d


# ─── Apply route integration ─────────────────────────────────


@pytest.fixture
def client(app):
    return app.test_client()


def _login_super(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"o5r_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o5-pass", full_name="O5",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o5-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token") or ""


def test_apply_blocked_when_safety_blocks(app, client, monkeypatch):
    """Critical alert + no override → safety check blocks
    before reaching the wire. Tests the apply route doesn't
    touch the fake client when safety refuses."""
    _seed_nas(app, nas_id=20)
    _login_super(client)
    # Open a critical alert so safety check escalates to blocked.
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o5:20",
            router_id=20, title_ar="crit", severity="critical",
        )

    # Stub state so the planner can produce a plan.
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether"}]))
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]))
    monkeypatch.setattr(
        mac, "ip_routes",
        lambda nas: MtResult(ok=True, data=[]))

    # Track whether the wire client was opened — it must NOT be.
    touched: list[bool] = []
    from app.radius.routes import mt_programming as routes_pkg

    class _FakeClient:
        def connect(self): touched.append(True)
        def close(self): pass
        def run(self, *a, **kw): touched.append(True); return []

    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: _FakeClient())

    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/20/program/apply",
        data={"_csrf_token": token,
              "kind": "hotspot",
              "interface": "ether2",
              "cidr": "192.168.10.0/24",
              "hotspot_name": "hs",
              "dns_servers": "8.8.8.8,1.1.1.1",
              "confirm": "1"},
    )
    assert res.status_code == 200
    # Wire client never opened.
    assert touched == []
    html = res.get_data(as_text=True)
    # Some Arabic refusal text appears in the page.
    assert "محظورة" in html or "تأكيدًا صريحًا" in html
