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


def test_zero_touch_discovery_creates_unassigned_device(app):
    """جهاز يظهر في GenieACS بلا رمز تسجيل → يُنشأ تلقائيًّا (discovered) غير مربوط."""
    with app.app_context():
        from app.radius.services.tr069 import sync_service
        from app.radius.db.repos import tr069_repo
        gd = {"_id": "OUI-Pc-ZT1", "_lastInform": "2026-07-16T10:00:00Z",
              "_deviceId": {"SerialNumber": "ZT1", "ProductClass": "Pc", "OUI": "OUI"},
              "InternetGatewayDevice": {"DeviceInfo": {
                  "Manufacturer": {"_value": "MikroTik"},
                  "ModelName": {"_value": "hAP"}}}}
        assert sync_service._reconcile_one(1, gd) is True
        dev = tr069_repo.get_by_acs_id(1, "OUI-Pc-ZT1")
        assert dev and dev["origin"] == "discovered" and dev["status"] == "active"
        assert dev["radius_username"] == "" and dev["subscriber_id"] is None
        counts = tr069_repo.count_devices(1)
        assert counts["unassigned"] == 1


def test_serial_binding_auto_links_on_discovery(app):
    """سيريال مسجَّل مسبقًا → الجهاز يُربَط بالمشترك آليًّا عند أوّل اتصال."""
    with app.app_context():
        from app.radius.services.tr069 import sync_service
        from app.radius.db.repos import tr069_repo
        tr069_repo.create_serial_binding(
            1, serial_number="ZT2", radius_username="ahmad-home",
            subscriber_id=42, owner_admin_id=7)
        gd = {"_id": "OUI-Pc-ZT2", "_lastInform": "2026-07-16T10:00:00Z",
              "_deviceId": {"SerialNumber": "ZT2", "ProductClass": "Pc", "OUI": "OUI"},
              "InternetGatewayDevice": {"DeviceInfo": {}}}
        sync_service._reconcile_one(1, gd)
        dev = tr069_repo.get_by_acs_id(1, "OUI-Pc-ZT2")
        assert dev["radius_username"] == "ahmad-home" and dev["subscriber_id"] == 42
        assert dev["owner_admin_id"] == 7


def test_serial_binding_links_previously_discovered_device(app):
    """أُضيف السيريال بعد ظهور الجهاز → يُربَط في الجولة التالية."""
    with app.app_context():
        from app.radius.services.tr069 import sync_service
        from app.radius.db.repos import tr069_repo
        gd = {"_id": "OUI-Pc-ZT3", "_lastInform": "2026-07-16T10:00:00Z",
              "_deviceId": {"SerialNumber": "ZT3", "ProductClass": "Pc", "OUI": "OUI"},
              "InternetGatewayDevice": {"DeviceInfo": {}}}
        sync_service._reconcile_one(1, gd)  # discovered, unassigned
        tr069_repo.create_serial_binding(1, serial_number="ZT3",
                                         radius_username="late-bind")
        sync_service._reconcile_one(1, gd)  # second inform → auto-link
        dev = tr069_repo.get_by_acs_id(1, "OUI-Pc-ZT3")
        assert dev["radius_username"] == "late-bind"


def test_setup_page_shows_shared_acs_and_bindings(app, client):
    r = client.get("/admin/radius/routers/setup")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "ACS URL" in html and "الربط المسبق" in html
    # add a binding via the form
    r2 = client.post("/admin/radius/routers/serial-binding",
                     data={"serial_number": "SB1", "radius_username": "u-sb1",
                           "_csrf_token": "tok"}, follow_redirects=True)
    assert "u-sb1" in r2.get_data(as_text=True) or r2.status_code == 200
    with app.app_context():
        from app.radius.db.repos import tr069_repo
        assert tr069_repo.get_serial_binding(1, "SB1")["radius_username"] == "u-sb1"


def test_online_from_inform_age_and_internet_status(app):
    """online يُحسب من عمر آخر Inform؛ internet من ConnectionStatus/WAN IP."""
    with app.app_context():
        from app.radius.services.tr069 import sync_service
        from app.radius.db.repos import tr069_repo
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc).isoformat()
        gd = {"_id": "OUI-Pc-ON1", "_lastInform": fresh,
              "_deviceId": {"SerialNumber": "ON1", "ProductClass": "Pc", "OUI": "OUI"},
              "InternetGatewayDevice": {
                  "DeviceInfo": {},
                  "WANDevice": {"1": {"WANConnectionDevice": {"1": {
                      "WANPPPConnection": {"1": {
                          "ConnectionStatus": {"_value": "Connected"},
                          "ExternalIPAddress": {"_value": "197.5.5.5"}}}}}}}}}
        sync_service._reconcile_one(1, gd, offline_sec=600)
        dev = tr069_repo.get_by_acs_id(1, "OUI-Pc-ON1")
        assert dev["is_online"] == 1 and dev["internet_status"] == "up"

        # آخر Inform قديم جدًّا → offline، internet unknown
        old = "2020-01-01T00:00:00Z"
        gd2 = dict(gd, _id="OUI-Pc-OFF1", _lastInform=old,
                   _deviceId={"SerialNumber": "OFF1", "ProductClass": "Pc", "OUI": "OUI"})
        sync_service._reconcile_one(1, gd2, offline_sec=600)
        dev2 = tr069_repo.get_by_acs_id(1, "OUI-Pc-OFF1")
        assert dev2["is_online"] == 0 and dev2["internet_status"] == "unknown"


def test_offline_and_no_internet_alerts_fire_on_transition(app, monkeypatch):
    """انتقال online→offline و up→down يُطلق تنبيهًا عبر admin_alerts (مرّة)."""
    sent = []
    with app.app_context():
        from app.radius.services.tr069 import alerts as tr_alerts
        from app.radius.db.repos import tr069_repo
        monkeypatch.setattr("app.radius.services.admin_alerts.dispatch",
                            lambda tid, key, ctx=None, **kw: sent.append((key, ctx)))
        did = tr069_repo.create_device(
            1, acs_device_id="AL1", status="active", radius_username="u-al1",
            is_online=1, internet_status="up", is_managed=1,
            last_online_change_at="2026-07-16T00:00:00Z")
        row = tr069_repo.get_device(1, did)
        # online→offline
        tr_alerts.evaluate(1, row, now_online=False, new_internet="unknown",
                           fields={"serial_number": "S1"}, offline_minutes=15)
        # up→down (still online)
        tr_alerts.evaluate(1, row, now_online=True, new_internet="down",
                           fields={"serial_number": "S1"})
        keys = [k for k, _ in sent]
        assert "router_device_offline" in keys
        assert "router_device_no_internet" in keys


def test_first_ever_online_does_not_alert_recovery(app, monkeypatch):
    """أوّل اتصال إطلاقًا لا يُطلق «عاد الاتصال» (لا last_online_change_at)."""
    sent = []
    with app.app_context():
        from app.radius.services.tr069 import alerts as tr_alerts
        from app.radius.db.repos import tr069_repo
        monkeypatch.setattr("app.radius.services.admin_alerts.dispatch",
                            lambda tid, key, ctx=None, **kw: sent.append(key))
        did = tr069_repo.create_device(1, acs_device_id="AL2", status="active",
                                       is_online=0, is_managed=1)
        row = tr069_repo.get_device(1, did)
        tr_alerts.evaluate(1, row, now_online=True, new_internet="up", fields={})
        assert "router_device_online" not in sent


def test_router_alert_specs_registered():
    from app.radius.services import admin_alerts
    for k in ("router_device_offline", "router_device_online",
              "router_device_no_internet", "router_device_internet_back"):
        assert admin_alerts.get_spec(k) is not None
        assert admin_alerts.preview(k)  # renders with sample


def test_health_json_reports_offline_and_no_internet(app, client):
    """نقطة الاستطلاع الحيّ تُرجع الأجهزة المفصولة/بلا إنترنت فقط."""
    with app.app_context():
        from app.radius.db.repos import tr069_repo
        tr069_repo.create_device(1, acs_device_id="H1", status="active",
                                 radius_username="ok-dev", is_online=1,
                                 internet_status="up")
        tr069_repo.create_device(1, acs_device_id="H2", status="active",
                                 radius_username="off-dev", is_online=0)
        tr069_repo.create_device(1, acs_device_id="H3", status="active",
                                 radius_username="noi-dev", is_online=1,
                                 internet_status="down")
    data = client.get("/admin/radius/routers/health.json").get_json()
    assert data["ok"] is True and data["count"] == 2
    by = {i["name"]: i["issue"] for i in data["issues"]}
    assert by.get("off-dev") == "offline" and by.get("noi-dev") == "no_internet"
    assert "ok-dev" not in by


def test_permissions_registered():
    from app.radius.core.constants import ALL_PERMISSIONS
    for p in ("routers.view", "routers.manage", "routers.reboot",
              "routers.change_wifi", "routers.change_pppoe", "routers.factory_reset"):
        assert p in ALL_PERMISSIONS
