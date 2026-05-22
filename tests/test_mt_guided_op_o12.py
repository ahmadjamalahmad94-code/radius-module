"""O12 — Guided operations assistant."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o12_")
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
    u = f"o12_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o12-pass", full_name="O12",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o12-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


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
                (nas_id, f"o12-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", 1 if enabled else 0, now),
            )


# ─── Service ────────────────────────────────────────────────


def test_unknown_router_returns_none(app):
    with app.app_context():
        from app.radius.services.mt_guided_op import (
            OP_BACKUP_SAVE, build_checklist,
        )
        out = build_checklist(
            tenant_id=1, nas_id=99999, admin=None,
            operation=OP_BACKUP_SAVE,
        )
    assert out is None


def test_default_operation_for_unknown_op_string(app):
    _seed_nas(app, nas_id=1)
    with app.app_context():
        from app.radius.services.mt_guided_op import build_checklist
        out = build_checklist(
            tenant_id=1, nas_id=1, admin=None,
            operation="totally-not-real",
        )
    assert out is not None
    # Fell back to a known supported op (backup_save per the
    # service's safe default).
    from app.radius.services.mt_guided_op import (
        ALL_OPERATIONS,
    )
    assert out.operation in ALL_OPERATIONS


def test_disabled_router_blocks_on_health(app):
    _seed_nas(app, nas_id=2, enabled=False)
    with app.app_context():
        from app.radius.services.mt_guided_op import (
            OP_PROGRAMMING_HOTSPOT, build_checklist,
        )
        out = build_checklist(
            tenant_id=1, nas_id=2, admin=None,
            operation=OP_PROGRAMMING_HOTSPOT,
        )
    assert out is not None
    assert out.can_proceed is False
    health_step = [s for s in out.steps if s.key == "health"][0]
    assert health_step.state == "blocking"


def test_safety_blocks_when_admin_lacks_permission(app):
    _seed_nas(app, nas_id=3)
    with app.app_context():
        from app.radius.services.mt_guided_op import (
            OP_PROGRAMMING_HOTSPOT, build_checklist,
        )
        out = build_checklist(
            tenant_id=1, nas_id=3, admin=None,
            operation=OP_PROGRAMMING_HOTSPOT,
        )
    assert out is not None
    safety = [s for s in out.steps if s.key == "safety"][0]
    # Without an admin DTO, required_perm is not held → blocked.
    assert safety.state == "blocking"
    assert out.can_proceed is False


def test_backup_step_missing_blocks_programming(app):
    _seed_nas(app, nas_id=4)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.mt_guided_op import (
            OP_PROGRAMMING_HOTSPOT, build_checklist,
        )
        admin = admins_repo.create_admin(
            username=f"o12u_{uuid4().hex[:5]}",
            password="pw", full_name="x",
            is_super_admin=True,
        )
        out = build_checklist(
            tenant_id=1, nas_id=4, admin=admin,
            operation=OP_PROGRAMMING_HOTSPOT,
        )
    assert out is not None
    backup = [s for s in out.steps if s.key == "backup"][0]
    assert backup.state == "blocking"
    assert "نسخة" in backup.detail_ar


def test_backup_step_missing_is_info_for_backup_save_op(app):
    _seed_nas(app, nas_id=5)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.services.mt_guided_op import (
            OP_BACKUP_SAVE, build_checklist,
        )
        admin = admins_repo.create_admin(
            username=f"o12u_{uuid4().hex[:5]}",
            password="pw", full_name="x",
            is_super_admin=True,
        )
        out = build_checklist(
            tenant_id=1, nas_id=5, admin=admin,
            operation=OP_BACKUP_SAVE,
        )
    assert out is not None
    backup = [s for s in out.steps if s.key == "backup"][0]
    # Backup save = creating one, so missing isn't blocking.
    assert backup.state == "info"


def test_recent_failure_step_picks_up_partial_audit(app):
    _seed_nas(app, nas_id=6)
    with app.app_context():
        from app.radius.db.repos import (
            admins_repo, audit_repo, router_backups_repo as br,
        )
        from app.radius.services.mt_guided_op import (
            OP_PROGRAMMING_HOTSPOT, build_checklist,
        )
        admin = admins_repo.create_admin(
            username=f"o12u_{uuid4().hex[:5]}",
            password="pw", full_name="x",
            is_super_admin=True,
        )
        # Seed a fresh backup so the backup step doesn't block.
        br.record(tenant_id=1, router_id=6,
                   backup_type="binary",
                   filename="ok.backup", status="success")
        audit_repo.record(
            tenant_id=1, actor="x",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="6",
            router_id=6, severity="warning",
            result_status="partial",
        )
        out = build_checklist(
            tenant_id=1, nas_id=6, admin=admin,
            operation=OP_PROGRAMMING_HOTSPOT,
        )
    assert out is not None
    rf = [s for s in out.steps
          if s.key == "recent_failure"][0]
    assert rf.state == "warning"
    # The link points at the recovery page.
    assert "/recovery/" in rf.href


def test_can_proceed_true_when_all_green(app):
    _seed_nas(app, nas_id=7)
    with app.app_context():
        from app.radius.db.repos import (
            admins_repo, router_backups_repo as br,
        )
        from app.radius.services.mt_guided_op import (
            OP_PROGRAMMING_HOTSPOT, build_checklist,
        )
        admin = admins_repo.create_admin(
            username=f"o12u_{uuid4().hex[:5]}",
            password="pw", full_name="x",
            is_super_admin=True,
        )
        br.record(tenant_id=1, router_id=7,
                   backup_type="binary",
                   filename="ok.backup", status="success")
        out = build_checklist(
            tenant_id=1, nas_id=7, admin=admin,
            operation=OP_PROGRAMMING_HOTSPOT,
        )
    # Health may still be "unknown" without a snapshot — that's
    # info, not blocking. Other steps should be OK or warning.
    assert out is not None
    # Backup is present.
    backup = [s for s in out.steps if s.key == "backup"][0]
    assert backup.state == "ok"
    # No blocking step.
    assert not out.blocking_steps()
    assert out.can_proceed is True


# ─── Route ──────────────────────────────────────────────────


def test_assistant_route_login_guarded(client):
    res = client.get("/admin/radius/mt/1/assistant",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_assistant_route_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/assistant")
    assert res.status_code == 404


def test_assistant_route_renders_checklist(app, client):
    _seed_nas(app, nas_id=10)
    _login(client)
    res = client.get(
        "/admin/radius/mt/10/assistant?op=programming_hotspot")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-guided-op" in html
    assert 'data-mt-guided-nas="10"' in html
    assert 'data-mt-guided-operation="programming_hotspot"' in html
    # Every step keyed in the service appears in HTML.
    for key in ("health", "safety", "backup",
                 "recent_failure", "apply_link"):
        assert f'data-mt-guided-step="{key}"' in html


def test_assistant_unknown_op_falls_back_to_default(app, client):
    _seed_nas(app, nas_id=11)
    _login(client)
    res = client.get(
        "/admin/radius/mt/11/assistant?op=garbage-value")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Falls back to programming_hotspot (route's default).
    assert 'data-mt-guided-operation="programming_hotspot"' in html
