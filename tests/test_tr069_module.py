# -*- coding: utf-8 -*-
"""إدارة الراوترات عن بُعد (TR-069) — وحدة تجريبيّة. يغطّي: بوّابة العلم،
التسجيل، جدولة الأوامر، عزل الملكيّة، والعامل الخلفيّ عند تعطّل GenieACS.
شغّل الملف وحده."""
from __future__ import annotations

import os
import re
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(d, "tr069.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("FLASK_SECRET", "testkey")
    monkeypatch.setenv("HOBERADIUS_TR069_ENABLED", "1")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo, admins_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute("UPDATE admins SET is_super_admin=1 WHERE id=1")
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t",
                 permissions=[], _csrf_token="tok")
    return c


def _enroll(client, username="0591234567"):
    r = client.post("/admin/radius/routers/enroll",
                    data={"radius_username": username, "_csrf_token": "tok"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    return int(re.search(r"/routers/(\d+)", loc).group(1)), loc


def test_migration_created_tables(app):
    with app.app_context():
        from app.radius.db.connection import db
        for t in ("tr069_devices", "tr069_device_actions", "tr069_device_snapshots",
                  "tr069_model_profiles", "tr069_events"):
            assert db().execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] == 0


def test_flag_gates_the_module(app, client, monkeypatch):
    assert client.get("/admin/radius/routers").status_code == 200
    monkeypatch.setenv("HOBERADIUS_TR069_ENABLED", "0")
    assert client.get("/admin/radius/routers").status_code == 404


def test_enroll_creates_pending_device_with_one_time_reveal(app, client):
    device_id, loc = _enroll(client)
    html = client.get(loc).get_data(as_text=True)
    assert "مرّة واحدة" in html          # one-time credential reveal
    assert "ACS URL" in html
    with app.app_context():
        from app.radius.db.repos import tr069_repo
        dev = tr069_repo.get_device(1, device_id)
        assert dev["status"] == "pending"
        assert dev["radius_username"] == "0591234567"
        # secrets stored ENCRYPTED, never blank-plain
        assert dev["cwmp_password_enc"] and dev["cwmp_password_enc"] != ""
        # a second GET no longer reveals (session popped)
    html2 = client.get(loc).get_data(as_text=True)
    assert "مرّة واحدة" not in html2


def test_action_rejected_until_device_active(app, client):
    device_id, _ = _enroll(client)
    # pending device → action rejected
    from app.radius.services.tr069.action_service import Tr069ActionService, Tr069ActionError
    with app.app_context():
        with pytest.raises(Tr069ActionError):
            Tr069ActionService(1).queue(device_id=device_id, action_type="reboot",
                                        actor="t")


def test_action_queues_on_active_device(app, client):
    device_id, _ = _enroll(client)
    with app.app_context():
        from app.radius.db.repos import tr069_repo
        tr069_repo.update_device(1, device_id, status="active", acs_device_id="OUI-PC-SN1")
    r = client.post(f"/admin/radius/routers/{device_id}/action",
                    data={"action_type": "reboot", "_csrf_token": "tok"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import tr069_repo
        acts = tr069_repo.list_actions(1, device_id)
        assert len(acts) == 1 and acts[0]["action_type"] == "reboot"
        assert acts[0]["status"] == "queued"


def test_change_wifi_stores_no_plaintext_password(app, client):
    device_id, _ = _enroll(client)
    with app.app_context():
        from app.radius.db.repos import tr069_repo
        from app.radius.services.tr069.action_service import Tr069ActionService
        tr069_repo.update_device(1, device_id, status="active", acs_device_id="OUI-PC-SN2")
        Tr069ActionService(1).queue(device_id=device_id, action_type="change_wifi",
                                    params={"ssid": "MyNet", "password": "s3cret-pass"},
                                    actor="t")
        act = tr069_repo.list_actions(1, device_id)[0]
        # the stored request_payload masks the password; summary never shows it
        assert "s3cret-pass" not in act["request_payload"]
        assert "s3cret-pass" not in act["safe_summary"]


def test_ownership_isolation(app):
    """مدير غير مالك/سوبر لا يرى جهاز مالك آخر."""
    with app.app_context():
        from app.radius.services.tr069.device_service import Tr069DeviceService
        from app.radius.db.repos import tr069_repo
        did = tr069_repo.create_device(1, owner_admin_id=99, radius_username="u1",
                                       status="active", acs_device_id="X1")
        svc = Tr069DeviceService(1)
        # a non-super manager (id=5) cannot see owner-99's device
        assert svc.get_device(did, viewer_admin_id=5, can_view_all=False) is None
        assert len(svc.list_devices(viewer_admin_id=5, can_view_all=False)) == 0
        # super (can_view_all) sees it
        assert svc.get_device(did, viewer_admin_id=1, can_view_all=True) is not None


def test_worker_sweep_safe_when_genieacs_down(app):
    with app.app_context():
        from app.workers.tr069_action_worker import sweep_once
        # GenieACS unreachable in test → must not raise, returns structured result
        res = sweep_once()
        assert res["skipped"] is False and "reconciled" in res


def test_worker_skips_when_flag_off(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_TR069_ENABLED", "0")
    with app.app_context():
        from app.workers.tr069_action_worker import sweep_once
        assert sweep_once()["skipped"] is True


def test_permissions_registered():
    from app.radius.core.constants import ALL_PERMISSIONS
    for p in ("routers.view", "routers.manage", "routers.reboot",
              "routers.change_wifi", "routers.change_pppoe", "routers.factory_reset"):
        assert p in ALL_PERMISSIONS
