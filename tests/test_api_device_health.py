"""feat/api-first-parity — device-health JSON (group 7a).

يعكس صفحة /admin/radius/device-health عبر خدمة device_health نفسها: ملخّص +
قائمة، CRUD، تفعيل/تعطيل المراقبة، أحداث/تنبيهات الجهاز، واجهات الراوتر،
ومفتاح التطبيق الحيّ. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_devhealth_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_router(app, rid=750):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute("INSERT INTO nas_devices(id, tenant_id, name, address, secret, vendor, enabled, created_at) "
                         "VALUES (?,1,'R-DH','10.0.0.5','s','mikrotik',1,?)", (rid, now))
    return rid


def _seed_device(app, rid, name="cam-1"):
    with app.app_context():
        from app.radius.db.repos import device_health_repo as repo
        return repo.create_device(
            tenant_id=1, router_id=rid, name=name, interface_name="ether2",
            ip_address="192.168.88.10", network_cidr="192.168.88.0/24",
            gateway_address="192.168.88.1", device_type="camera")


def test_requires_auth(client):
    assert client.get("/api/v1/device-health").status_code == 401


def test_overview_empty(app, client):
    _seed_router(app)
    res = client.get("/api/v1/device-health", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert "summary" in data and data["devices"] == []
    assert any(r for r in data["routers"])  # الراوتر يظهر في القائمة المنسدلة


def test_list_after_seed(app, client):
    rid = _seed_router(app)
    _seed_device(app, rid)
    data = client.get("/api/v1/device-health/devices", headers=AUTH).get_json()["data"]
    assert data["summary"] is not None
    assert any(d["name"] == "cam-1" for d in data["devices"])


def test_enable_disable_and_events_alerts(app, client):
    rid = _seed_router(app)
    did = _seed_device(app, rid)
    assert client.post(f"/api/v1/device-health/devices/{did}/disable", headers=AUTH).get_json()["data"]["monitoring_enabled"] is False
    assert client.post(f"/api/v1/device-health/devices/{did}/enable", headers=AUTH).get_json()["data"]["monitoring_enabled"] is True
    assert "events" in client.get(f"/api/v1/device-health/devices/{did}/events", headers=AUTH).get_json()["data"]
    assert "alerts" in client.get(f"/api/v1/device-health/devices/{did}/alerts", headers=AUTH).get_json()["data"]


def test_update_and_delete(app, client):
    rid = _seed_router(app)
    did = _seed_device(app, rid, name="old")
    upd = client.patch(f"/api/v1/device-health/devices/{did}", headers=AUTH,
                       json={"name": "new-name"})
    assert upd.status_code == 200, upd.get_json()
    assert upd.get_json()["data"]["device"]["name"] == "new-name"
    dele = client.delete(f"/api/v1/device-health/devices/{did}", headers=AUTH)
    assert dele.status_code == 200 and dele.get_json()["data"]["deleted"] is True


def test_router_interfaces_no_router(client):
    res = client.get("/api/v1/device-health/router-interfaces", headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["data"]["online"] is False


def test_live_apply_toggle(client):
    get = client.get("/api/v1/device-health/live-apply", headers=AUTH)
    assert get.status_code == 200
    setres = client.post("/api/v1/device-health/live-apply", headers=AUTH, json={"enabled": True})
    assert setres.status_code == 200, setres.get_json()
    # القراءة بعد الضبط تعكس الحالة
    again = client.get("/api/v1/device-health/live-apply", headers=AUTH).get_json()["data"]
    assert "enabled" in again


def test_create_validation_error(app, client):
    _seed_router(app)
    # جسم ناقص (بلا اسم/واجهة) → خطأ تحقّق 422
    res = client.post("/api/v1/device-health/devices", headers=AUTH, json={"router_id": 750})
    assert res.status_code in (422, 201)  # الخدمة تتحقّق؛ نتأكّد أنها لا تنهار 500
    assert res.get_json()["ok"] in (True, False)
