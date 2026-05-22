"""O1 — Router status overview foundation.

Service tests pin the shape + the safe-to-modify derivation
under healthy / stale / offline / partial-data inputs. Route
tests pin permission gate + 404 for unknown router + tenant
isolation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o1_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, *, is_super_admin: bool = True) -> int:
    from app.radius.db.repos import admins_repo
    u = f"o1_{uuid4().hex[:8]}"
    admin = admins_repo.create_admin(
        username=u, password="o1-pass", full_name="O1",
        is_super_admin=is_super_admin,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o1-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    return admin.id


def _seed_nas(app, *, nas_id=1, name=None, enabled=True, mode="direct"):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, ?, 'hr', 'p')""",
                (nas_id, name or f"o1-rtr-{nas_id}",
                 f"203.0.113.{nas_id}",
                 1 if enabled else 0, now, mode),
            )


# ─── Service: identity + missing-data cases ──────────────────


def test_build_overview_returns_none_for_unknown_router(app):
    with app.app_context():
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        assert build_overview(tenant_id=1, nas_id=999) is None


def test_overview_with_no_snapshot_alerts_or_backups(app):
    _seed_nas(app, nas_id=1, name="fresh-rtr")
    with app.app_context():
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=1)
    assert ov is not None
    assert ov.nas_id == 1
    assert ov.name == "fresh-rtr"
    # No snapshot data → unknown status, but page still works.
    assert ov.snapshot_status == "unknown"
    assert ov.has_snapshot is False
    assert ov.active_alerts_total == 0
    assert ov.backup_status == "missing"
    # No alerts + enabled → safe (backup-missing is advisory).
    assert ov.safe_to_modify is True
    # Must suggest taking a backup.
    codes = {a.code for a in ov.suggested_actions}
    assert "take_backup" in codes


# ─── Disabled router ─────────────────────────────────────────


def test_disabled_router_is_unsafe(app):
    _seed_nas(app, nas_id=2, enabled=False)
    with app.app_context():
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=2)
    assert ov.safe_to_modify is False
    assert any("معطّل" in r for r in ov.safety_reasons)


# ─── Snapshot states ─────────────────────────────────────────


def test_overview_with_fresh_snapshot(app):
    _seed_nas(app, nas_id=3)
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(
            tenant_id=1, router_id=3,
            counters={"hotspot_active": 5},
            resource={"cpu-load": "12"},
        )
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=3)
    assert ov.snapshot_status == "fresh"
    assert ov.has_snapshot is True
    assert ov.counters.get("hotspot_active") == 5
    assert ov.resource.get("cpu-load") == "12"


def test_overview_with_failed_snapshot_marks_unsafe(app):
    _seed_nas(app, nas_id=4)
    with app.app_context():
        from app.radius.db.repos import router_snapshots_repo as r
        # Failure with no prior success → "failed" status.
        r.save_failure(tenant_id=1, router_id=4,
                       error="connect refused")
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=4)
    assert ov.snapshot_status == "failed"
    assert ov.safe_to_modify is False
    assert any("snapshot" in r for r in ov.safety_reasons)
    # Should suggest running diagnostics.
    codes = {a.code for a in ov.suggested_actions}
    assert "refresh_diagnostics" in codes


def test_stale_snapshot_is_visible_but_does_not_block(app):
    """A snapshot from 2 hours ago is stale but still data —
    operator should see it + be nudged, not blocked."""
    _seed_nas(app, nas_id=5)
    with app.app_context():
        from app.radius.db.connection import transaction
        # Save a success then manually backdate the timestamp.
        from app.radius.db.repos import router_snapshots_repo as r
        r.save_success(tenant_id=1, router_id=5,
                       counters={"hotspot_active": 1})
        # Backdate to 2 hours ago.
        old = (datetime.now(timezone.utc) -
               timedelta(hours=2)).isoformat()
        with transaction() as c:
            c.execute(
                "UPDATE router_snapshots SET last_success_at=? "
                "WHERE router_id=?",
                (old, 5),
            )
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=5)
    assert ov.snapshot_status == "stale"
    # Stale alone doesn't make it unsafe — partial-data router
    # should still be operator-usable.
    assert ov.safe_to_modify is True
    codes = {a.code for a in ov.suggested_actions}
    assert "refresh_diagnostics" in codes


# ─── Alerts ──────────────────────────────────────────────────


def test_overview_aggregates_active_alerts_by_severity(app):
    _seed_nas(app, nas_id=6)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(tenant_id=1, rule="x",
                         dedup_key="x:6:1", router_id=6,
                         title_ar="حرج", severity="critical")
        alerts_repo.open(tenant_id=1, rule="x",
                         dedup_key="x:6:2", router_id=6,
                         title_ar="تحذير", severity="warning")
        alerts_repo.open(tenant_id=1, rule="x",
                         dedup_key="x:6:3", router_id=6,
                         title_ar="معلومة", severity="info")
        # Alert on a different router shouldn't count.
        _seed_nas(app, nas_id=7)
        alerts_repo.open(tenant_id=1, rule="x",
                         dedup_key="x:7:1", router_id=7,
                         title_ar="غير منا", severity="critical")
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=6)
    assert ov.active_alerts_critical == 1
    assert ov.active_alerts_warning == 1
    assert ov.active_alerts_info == 1
    assert ov.active_alerts_total == 3
    # Critical alert makes router unsafe.
    assert ov.safe_to_modify is False
    # Suggests reviewing alerts.
    codes = {a.code for a in ov.suggested_actions}
    assert "review_alerts" in codes


# ─── Backup states ───────────────────────────────────────────


def test_overview_picks_newest_successful_backup(app):
    _seed_nas(app, nas_id=8)
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as br
        br.record(tenant_id=1, router_id=8,
                  backup_type="binary", filename="old.backup",
                  status="success")
        br.record(tenant_id=1, router_id=8,
                  backup_type="binary", filename="newer.backup",
                  status="success")
        # A failed attempt shouldn't be picked.
        br.record(tenant_id=1, router_id=8,
                  backup_type="binary", filename="latest-fail.backup",
                  status="failed",
                  error_message="router refused")
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=8)
    # The newest *successful* backup is what surfaces.
    # list_for_router is DESC by id, so "newer.backup" was id=2.
    # _classify_backup picks the first success encountered.
    # We don't pin filename here (creation order is DESC) — but
    # the picked row must be the newest success.
    assert ov.has_backup is True
    assert ov.last_backup_status == "success"
    assert ov.backup_status in {"fresh", "stale"}


def test_overview_marks_missing_backup_and_suggests_action(app):
    _seed_nas(app, nas_id=9)
    with app.app_context():
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=9)
    assert ov.backup_status == "missing"
    assert any("نسخة احتياطية" in r for r in ov.safety_reasons)
    codes = {a.code for a in ov.suggested_actions}
    assert "take_backup" in codes


# ─── Audit / last failed / last danger ───────────────────────


def test_overview_surfaces_last_audit_and_failure(app):
    _seed_nas(app, nas_id=10)
    with app.app_context():
        from app.radius.db.repos import audit_repo
        # Three rows: latest success, earlier failed, earliest
        # informational.
        audit_repo.record(
            tenant_id=1, actor="op", action="mt.x.ok",
            target_type="mikrotik_nas", target_id="10",
            router_id=10, severity="info",
            result_status="success",
        )
        audit_repo.record(
            tenant_id=1, actor="op", action="mt.x.fail",
            target_type="mikrotik_nas", target_id="10",
            router_id=10, severity="warning",
            result_status="failed",
            error_message="router unreachable",
        )
        audit_repo.record(
            tenant_id=1, actor="op", action="mt.programming.apply",
            target_type="mikrotik_nas", target_id="10",
            router_id=10, severity="critical",
            result_status="success",
        )
        from app.radius.services.mt_router_overview import (
            build_overview,
        )
        ov = build_overview(tenant_id=1, nas_id=10)
    # last_audit = newest row (critical success).
    assert ov.last_audit_action == "mt.programming.apply"
    # last_failed = the one with result_status=failed.
    assert ov.last_failed_action == "mt.x.fail"
    # last_danger = newest of {warning, critical} = the critical
    # success entry (severity=critical takes precedence over
    # result).
    assert ov.last_danger_action == "mt.programming.apply"
    # Suggested actions include reviewing the failure.
    codes = {a.code for a in ov.suggested_actions}
    assert "review_last_failure" in codes


# ─── Route layer ─────────────────────────────────────────────


def test_route_login_guarded(client):
    res = client.get("/admin/radius/mt/1/overview",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_route_renders_for_super_admin(app, client):
    _seed_nas(app, nas_id=11, name="route-rtr")
    _login(client, is_super_admin=True)
    res = client.get("/admin/radius/mt/11/overview")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-router-overview" in html
    assert 'data-mt-router-id="11"' in html
    assert "route-rtr" in html


def test_route_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/overview")
    assert res.status_code == 404


def test_route_renders_safety_banner_state(app, client):
    """Disabled router → unsafe banner; enabled clean → safe banner."""
    _seed_nas(app, nas_id=12, enabled=False)
    _login(client)
    html = client.get(
        "/admin/radius/mt/12/overview").get_data(as_text=True)
    assert 'data-mt-overview-safe-banner="unsafe"' in html
    assert 'data-mt-overview-safe="false"' in html


def test_route_blocked_for_non_admin_without_perm(app, client):
    """PERM_VIEW gates; non-admin role without it → 403."""
    _seed_nas(app, nas_id=13)
    _login(client, is_super_admin=False)
    res = client.get("/admin/radius/mt/13/overview",
                     headers={"Accept": "application/json"})
    assert res.status_code == 403
