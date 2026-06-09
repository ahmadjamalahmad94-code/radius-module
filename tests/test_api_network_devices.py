from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

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
    assert "/api/v1/network-devices/scan" in routes
    assert "/api/v1/network-devices/scan/add" in routes
    assert "/api/v1/network-devices/<int:device_id>/bypass" in routes
    assert "/api/v1/network-devices/<int:device_id>/bypass/apply" in routes
    assert "/api/v1/network-devices/<int:device_id>/bypass/remove" in routes
    assert "/api/v1/network-devices/<int:device_id>/remote-access" in routes
    assert "/api/v1/network-devices/<int:device_id>/remote-access/open" in routes
    assert (
        "/api/v1/network-devices/<int:device_id>/remote-access/"
        "<int:session_id>/close"
    ) in routes


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


def test_network_scan_api_returns_router_discoveries_and_known_flags(app, client, monkeypatch):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import network_devices_repo

        network_devices_repo.create(
            tenant_id=1,
            router_id=11,
            name="known camera",
            device_type="camera",
            ip_address="10.0.0.50",
            mac_address="AA:BB:CC:DD:EE:50",
            watch_enabled=True,
        )

    from app.radius.services import network_ip_scan

    captured = {}

    def fake_scan(nas):
        captured["nas"] = nas
        return SimpleNamespace(
            ok=True,
            error="",
            data=[
                {
                    "ip": "10.0.0.50",
                    "mac": "AA:BB:CC:DD:EE:50",
                    "hostname": "known-camera",
                    "interface": "ether2",
                    "vendor": "camera",
                    "sources": ["arp", "dhcp"],
                },
                {
                    "ip": "10.0.0.77",
                    "mac": "AA:BB:CC:DD:EE:77",
                    "hostname": "new-ap",
                    "interface": "ether3",
                    "vendor": "ap",
                    "sources": ["neighbor"],
                },
            ],
        )

    monkeypatch.setattr(network_ip_scan, "scan_router", fake_scan)

    res = client.post(
        "/api/v1/network-devices/scan",
        headers=AUTH,
        json={"router_id": 11},
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["router"]["id"] == 11
    assert data["count"] == 2
    assert data["items"][0]["known"] is True
    assert data["items"][1]["known"] is False
    assert data["known_ips"] == ["10.0.0.50"]
    assert captured["nas"]["api_password"] == "pw"
    assert "api_password" not in str(data)


def test_network_scan_add_registers_discovered_device(app, client):
    _seed_router(app)

    res = client.post(
        "/api/v1/network-devices/scan/add",
        headers=AUTH,
        json={
            "router_id": 11,
            "ip": "10.0.0.77",
            "mac": "AA:BB:CC:DD:EE:77",
            "hostname": "new-ap",
        },
    )
    assert res.status_code == 201, res.get_json()
    device = res.get_json()["data"]["device"]
    assert device["name"] == "new-ap"
    assert device["ip_address"] == "10.0.0.77"
    assert device["mac_address"] == "aa:bb:cc:dd:ee:77"
    assert device["watch_enabled"] is True


def test_network_device_bypass_api_uses_planner_without_exposing_router_secret(
    app,
    client,
    monkeypatch,
):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import network_devices_repo

        device_id = network_devices_repo.create(
            tenant_id=1,
            router_id=11,
            name="trusted ap",
            device_type="ap",
            ip_address="10.0.0.88",
            mac_address="AA:BB:CC:DD:EE:88",
            watch_enabled=True,
        )

    from app.radius.services import network_device_bypass_planner as planner

    calls = {}

    def fake_list(nas):
        calls["list_nas"] = nas
        return SimpleNamespace(
            ok=True,
            error="",
            data=[{"name": "dhcp-main", "interface": "bridge", "disabled": False}],
        )

    def fake_apply(**kwargs):
        calls["apply"] = kwargs
        return SimpleNamespace(
            ok=True,
            error="",
            data={"dhcp_lease": "ok", "ip_binding": "ok", "address_list": "ok"},
        )

    def fake_remove(**kwargs):
        calls["remove"] = kwargs
        return SimpleNamespace(
            ok=True,
            error="",
            data={"dhcp_lease": 1, "ip_binding": 1, "address_list": 1},
        )

    monkeypatch.setattr(planner, "list_dhcp_servers", fake_list)
    monkeypatch.setattr(planner, "apply_bypass", fake_apply)
    monkeypatch.setattr(planner, "remove_bypass", fake_remove)

    state = client.get(f"/api/v1/network-devices/{device_id}/bypass", headers=AUTH)
    assert state.status_code == 200, state.get_json()
    state_data = state.get_json()["data"]
    assert state_data["ready"] is True
    assert state_data["dhcp_servers"][0]["name"] == "dhcp-main"
    assert "api_password" not in str(state_data)

    apply = client.post(
        f"/api/v1/network-devices/{device_id}/bypass/apply",
        headers=AUTH,
        json={
            "dhcp_server_name": "dhcp-main",
            "bypass_hotspot": True,
            "add_to_address_list": True,
        },
    )
    assert apply.status_code == 200, apply.get_json()
    assert apply.get_json()["data"]["steps"]["ip_binding"] == "ok"
    assert calls["apply"]["dhcp_server_name"] == "dhcp-main"
    assert calls["apply"]["device"]["id"] == device_id
    assert calls["apply"]["nas"]["api_password"] == "pw"

    remove = client.post(
        f"/api/v1/network-devices/{device_id}/bypass/remove",
        headers=AUTH,
    )
    assert remove.status_code == 200, remove.get_json()
    assert remove.get_json()["data"]["total_removed"] == 3
    assert calls["remove"]["device_id"] == device_id
    assert "api_password" not in str(remove.get_json()["data"])


def test_network_device_remote_access_api_opens_and_closes_ttl_sessions(
    app,
    client,
    monkeypatch,
):
    monkeypatch.setenv("HOBERADIUS_VPS_PUBLIC_IP", "203.0.113.10")
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            network_devices_repo,
            remote_access_sessions_repo,
        )

        device_id = network_devices_repo.create(
            tenant_id=1,
            router_id=11,
            name="camera",
            device_type="camera",
            ip_address="10.0.0.88",
            mac_address="AA:BB:CC:DD:EE:88",
            management_port=8080,
            watch_enabled=True,
        )

    from app.radius.services import remote_device_access

    calls = {}

    def fake_open(**kwargs):
        calls["open"] = kwargs
        return True, "", {
            "id": 44,
            "tenant_id": 1,
            "device_id": device_id,
            "router_id": 11,
            "requested_by": kwargs["requested_by"],
            "protocol": kwargs["protocol"],
            "internal_ip": kwargs["device"]["ip_address"],
            "internal_port": 8080,
            "external_port": 40044,
            "status": "active",
            "created_at": "2026-06-07T12:00:00Z",
            "expires_at": "2026-06-07T12:30:00Z",
            "closed_at": "",
            "audit_ip": kwargs["audit_ip"],
            "notes": kwargs["notes"],
        }

    def fake_close(**kwargs):
        calls["close"] = kwargs
        with app.app_context():
            remote_access_sessions_repo.mark_closed(
                int(kwargs["session"]["id"]),
                status="closed",
            )
        return True, ""

    monkeypatch.setattr(remote_device_access, "open_session", fake_open)
    monkeypatch.setattr(remote_device_access, "close_session", fake_close)

    state = client.get(
        f"/api/v1/network-devices/{device_id}/remote-access",
        headers=AUTH,
    )
    assert state.status_code == 200, state.get_json()
    assert state.get_json()["data"]["config_ready"] is True
    assert state.get_json()["data"]["public_host"] == "203.0.113.10"
    assert "api_password" not in str(state.get_json()["data"])

    opened = client.post(
        f"/api/v1/network-devices/{device_id}/remote-access/open",
        headers=AUTH,
        json={
            "protocol": "http",
            "ttl_minutes": 30,
            "notes": "maintenance",
        },
    )
    assert opened.status_code == 201, opened.get_json()
    open_data = opened.get_json()["data"]
    assert open_data["session"]["public_url"] == "http://203.0.113.10:40044/"
    assert calls["open"]["nas"]["api_password"] == "pw"
    assert calls["open"]["device"]["id"] == device_id
    assert calls["open"]["ttl_minutes"] == 30
    assert calls["open"]["notes"] == "maintenance"
    assert "api_password" not in str(open_data)

    with app.app_context():
        session_id = remote_access_sessions_repo.create(
            tenant_id=1,
            device_id=device_id,
            router_id=11,
            requested_by="test",
            protocol="http",
            internal_ip="10.0.0.88",
            internal_port=8080,
            external_port=40088,
            ttl_minutes=30,
        )

    closed = client.post(
        f"/api/v1/network-devices/{device_id}/remote-access/"
        f"{session_id}/close",
        headers=AUTH,
    )
    assert closed.status_code == 200, closed.get_json()
    assert calls["close"]["session"]["id"] == session_id
    assert closed.get_json()["data"]["active_count"] == 0
    assert "api_password" not in str(closed.get_json()["data"])


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
