from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_network_devices_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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


def _seed_router(app, router_id: int = 11) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password, created_at)
                   VALUES (?, 1, 'راوتر الاختبار', '10.0.0.1', 'secret',
                           'mikrotik', 'hotspot', 1, 'api', 'pw', ?)""",
                (router_id, now),
            )


def test_network_devices_routes_are_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/network-devices" in routes
    assert "/api/v1/network-devices/<int:device_id>/check" in routes


def test_network_device_can_be_created_edited_checked_and_deleted(app, client):
    _seed_router(app)

    create = client.post(
        "/api/v1/network-devices",
        headers=AUTH,
        json={
            "router_id": 11,
            "name": "كاميرا المدخل",
            "device_type": "camera",
            "ip_address": "127.0.0.1",
            "mac_address": "AA-BB-CC-DD-EE-FF",
            "management_port": 9,
            "watch_enabled": True,
            "alert_enabled": True,
            "is_critical": True,
        },
    )
    assert create.status_code == 201, create.get_json()
    device = create.get_json()["data"]["device"]
    assert device["name"] == "كاميرا المدخل"
    assert device["device_type_label"] == "كاميرا"
    assert device["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert device["router_name"] == "راوتر الاختبار"
    assert "secret" not in device

    device_id = device["id"]
    edited = client.patch(
        f"/api/v1/network-devices/{device_id}",
        headers=AUTH,
        json={"name": "كاميرا المدخل الرئيسية", "watch_enabled": False},
    )
    assert edited.status_code == 200, edited.get_json()
    assert edited.get_json()["data"]["device"]["name"] == "كاميرا المدخل الرئيسية"
    assert edited.get_json()["data"]["device"]["watch_enabled"] is False

    check = client.post(f"/api/v1/network-devices/{device_id}/check", headers=AUTH)
    assert check.status_code == 200, check.get_json()
    assert check.get_json()["data"]["status"] in {"up", "down"}
    assert check.get_json()["data"]["message"]

    listed = client.get("/api/v1/network-devices?q=المدخل", headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    payload = listed.get_json()["data"]
    assert payload["count"] == 1
    assert payload["summary"]["total"] == 1

    deleted = client.delete(f"/api/v1/network-devices/{device_id}", headers=AUTH)
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["data"]["removed"] is True


def test_network_device_validation_messages_are_arabic(app, client):
    _seed_router(app)

    missing_router = client.post(
        "/api/v1/network-devices",
        headers=AUTH,
        json={"name": "AP"},
    )
    assert missing_router.status_code == 422
    assert missing_router.get_json()["error"]["message"] == "اختر الراوتر التابع له الجهاز."

    missing_name = client.post(
        "/api/v1/network-devices",
        headers=AUTH,
        json={"router_id": 11, "name": ""},
    )
    assert missing_name.status_code == 422
    assert missing_name.get_json()["error"]["message"] == "اسم الجهاز مطلوب."

    not_found = client.get("/api/v1/network-devices/999", headers=AUTH)
    assert not_found.status_code == 404
    assert not_found.get_json()["error"]["message"] == "جهاز الشبكة غير موجود."
