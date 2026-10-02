"""O8 — Recovery plan foundation."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o8_")
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


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"o8_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o8-pass", full_name="O8",
        is_super_admin=True,
    )
    # owner-only bypass (6824f26): the is_super_admin flag alone no longer
    # bypasses RBAC, and the boot-time «admin» (d13fb302) is always the
    # min-id owner — so designate the tester as owner, as the licensing
    # panel does. Same fix as test_route_permissions_s3_2._login.
    admins_repo.set_designated_owners([u])
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o8-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, f"o8-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )


# ─── Service ────────────────────────────────────────────────


def test_unknown_audit_id_returns_none(app):
    with app.app_context():
        from app.radius.services.mt_recovery_plan import build_plan
        assert build_plan(tenant_id=1, audit_id=99999) is None


def test_successful_event_yields_no_recovery(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="op", action="mt.x",
            target_type="mikrotik_nas", target_id="1",
            router_id=1, severity="info",
            result_status="success",
        )
        from app.radius.services.mt_recovery_plan import build_plan
        assert build_plan(tenant_id=1, audit_id=aid) is None


def test_partial_apply_yields_plan_with_unprogram_steps(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="alice",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="42",
            router_id=42, severity="warning",
            result_status="partial",
        )
        from app.radius.services.mt_recovery_plan import build_plan
        plan = build_plan(tenant_id=1, audit_id=aid)
    assert plan is not None
    assert plan.router_id == 42
    assert plan.result_status == "partial"
    # Steps mention Unprogram for the hotspot kind.
    steps_text = " ".join(plan.suggested_steps_ar)
    assert "Unprogram" in steps_text or "تراجع" in steps_text
    assert "hoberadius:hotspot" in steps_text


def test_failed_backup_event_yields_plan(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="bob", action="mt.backup.save",
            target_type="mikrotik_nas", target_id="1",
            router_id=1, severity="critical",
            result_status="failed",
        )
        from app.radius.services.mt_recovery_plan import build_plan
        plan = build_plan(tenant_id=1, audit_id=aid)
    assert plan is not None
    text = " ".join(plan.suggested_steps_ar)
    assert "/file" in text or "اتصال" in text


def test_nearest_backup_picked_before_event(app):
    _seed_nas(app, nas_id=100)
    with app.app_context():
        from app.radius.db.repos import (
            audit_repo, router_backups_repo as br,
        )
        # Older backup before the event.
        br.record(tenant_id=1, router_id=100,
                   backup_type="binary", filename="old.backup",
                   status="success")
        # Failed event AFTER that backup.
        aid = audit_repo.record(
            tenant_id=1, actor="alice",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="100",
            router_id=100, severity="warning",
            result_status="partial",
        )
        # Newer backup AFTER the event (must NOT be picked).
        after_id = br.record(tenant_id=1, router_id=100,
                             backup_type="binary", filename="after.backup",
                             status="success")
        # Windows' wall clock ticks every ~15.6 ms, so the three rows above
        # can share one created_at and the strict «before the event»
        # comparison sees no candidate. Pin distinct, ordered timestamps.
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE router_backups SET created_at=? "
                      "WHERE filename='old.backup' AND router_id=100",
                      ("2026-01-01T10:00:00.000000Z",))
            c.execute("UPDATE audit_log SET created_at=? WHERE id=?",
                      ("2026-01-01T10:00:01.000000Z", aid))
            c.execute("UPDATE router_backups SET created_at=? WHERE id=?",
                      ("2026-01-01T10:00:02.000000Z", after_id))
        from app.radius.services.mt_recovery_plan import build_plan
        plan = build_plan(tenant_id=1, audit_id=aid)
    assert plan.nearest_backup is not None
    assert plan.nearest_backup["filename"] == "old.backup"


def test_no_backup_means_no_nearest(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="op",
            action="mt.programming.pppoe.apply",
            target_type="mikrotik_nas", target_id="55",
            router_id=55, severity="warning",
            result_status="failed",
        )
        from app.radius.services.mt_recovery_plan import build_plan
        plan = build_plan(tenant_id=1, audit_id=aid)
    assert plan.nearest_backup is None


def test_recovery_plan_route_login_guarded(client):
    res = client.get("/admin/radius/recovery/1",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_recovery_plan_route_404_for_unknown(app, client):
    _login(client)
    res = client.get("/admin/radius/recovery/99999")
    assert res.status_code == 404


def test_recovery_plan_route_renders(app, client):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="alice",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="42",
            router_id=42, severity="warning",
            result_status="partial",
        )
    _login(client)
    html = client.get(
        f"/admin/radius/recovery/{aid}").get_data(as_text=True)
    assert "data-mt-recovery-plan" in html
    assert f'data-mt-recovery-audit-id="{aid}"' in html
    assert "data-mt-recovery-steps" in html
