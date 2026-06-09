"""device_health routes — page render + JSON API (login-session + CSRF header).

NO live MikroTik mutation is exercised: create/update/enable/disable/delete and
the dry-run plan endpoint are all router-free.

Run individually:  pytest tests/test_device_health_routes.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_routes_")
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


CSRF = "dh-test-csrf"


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "tester"
        s["admin_name"] = "Tester"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["permissions"] = []
        s["_csrf_token"] = CSRF


def _hdr():
    return {"X-CSRFToken": CSRF, "Content-Type": "application/json"}


def test_page_route_registered_and_renders(app, client):
    _login(client)
    res = client.get("/admin/radius/device-health")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "تتبع حالة الأجهزة" in html
    assert "إضافة جهاز" in html


def test_create_device_via_api(app, client):
    _seed_router(app)
    _login(client)
    res = client.post(
        "/admin/radius/device-health/api/devices",
        headers=_hdr(),
        json={"router_id": 11, "name": "AP المدخل", "device_type": "ap",
              "interface_name": "ether2", "ip_address": "192.168.15.10",
              "location": "البرج"},
    )
    assert res.status_code == 201, res.get_data(as_text=True)
    body = res.get_json()
    assert body["ok"] is True
    assert body["device"]["network_cidr"] == "192.168.15.0/24"
    assert body["device"]["gateway_address"] == "192.168.15.254/24"

    # It shows up in the JSON list + summary.
    lst = client.get("/admin/radius/device-health/api/devices").get_json()
    assert lst["ok"] is True
    assert lst["summary"]["total"] == 1
    assert any(d["name"] == "AP المدخل" for d in lst["devices"])


def test_create_duplicate_blocked_via_api(app, client):
    _seed_router(app)
    _login(client)
    payload = {"router_id": 11, "name": "AP1", "interface_name": "ether2",
               "ip_address": "192.168.15.10"}
    first = client.post("/admin/radius/device-health/api/devices",
                        headers=_hdr(), json=payload)
    assert first.status_code == 201
    dup = client.post("/admin/radius/device-health/api/devices",
                      headers=_hdr(), json=dict(payload, name="AP1-dup"))
    assert dup.status_code == 400
    assert dup.get_json()["ok"] is False


def test_missing_interface_rejected_via_api(app, client):
    _seed_router(app)
    _login(client)
    res = client.post(
        "/admin/radius/device-health/api/devices",
        headers=_hdr(),
        json={"router_id": 11, "name": "x", "ip_address": "192.168.15.10"},
    )
    assert res.status_code == 400


def test_dry_run_plan_endpoint(app, client):
    _login(client)
    res = client.get(
        "/admin/radius/device-health/api/plan"
        "?interface=ether2&ip=192.168.15.10&subnet_prefix=24&gateway_last_octet=254")
    assert res.status_code == 200
    plan = res.get_json()["plan"]
    assert plan["valid"] is True
    assert plan["live"] is False
    assert plan["network"]["gateway_address"] == "192.168.15.254/24"
    actions = {it["kind"]: it["action"] for it in plan["items"]}
    assert actions == {"ip_address": "planned", "ip_binding": "planned",
                       "netwatch": "planned"}


def test_dry_run_plan_invalid_ip_is_400(app, client):
    _login(client)
    res = client.get("/admin/radius/device-health/api/plan?interface=ether2&ip=bad")
    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_enable_disable_and_delete_via_api(app, client):
    _seed_router(app)
    _login(client)
    created = client.post(
        "/admin/radius/device-health/api/devices",
        headers=_hdr(),
        json={"router_id": 11, "name": "AP", "interface_name": "ether2",
              "ip_address": "192.168.15.10"},
    ).get_json()
    did = created["device"]["id"]

    dis = client.post(f"/admin/radius/device-health/api/devices/{did}/disable",
                      headers=_hdr())
    assert dis.status_code == 200
    assert dis.get_json()["device"]["monitoring_enabled"] is False

    en = client.post(f"/admin/radius/device-health/api/devices/{did}/enable",
                     headers=_hdr())
    assert en.get_json()["device"]["monitoring_enabled"] is True

    dele = client.post(f"/admin/radius/device-health/api/devices/{did}/delete",
                       headers=_hdr())
    assert dele.status_code == 200
    # Gone from the list after soft-delete.
    lst = client.get("/admin/radius/device-health/api/devices").get_json()
    assert all(d["id"] != did for d in lst["devices"])


def test_update_device_recomputes_network(app, client):
    _seed_router(app)
    _login(client)
    created = client.post(
        "/admin/radius/device-health/api/devices",
        headers=_hdr(),
        json={"router_id": 11, "name": "AP", "interface_name": "ether2",
              "ip_address": "192.168.15.10"},
    ).get_json()
    did = created["device"]["id"]
    upd = client.patch(
        f"/admin/radius/device-health/api/devices/{did}",
        headers=_hdr(),
        json={"ip_address": "10.0.5.20", "subnet_prefix": 24},
    )
    assert upd.status_code == 200, upd.get_data(as_text=True)
    dev = upd.get_json()["device"]
    assert dev["network_cidr"] == "10.0.5.0/24"
    assert dev["gateway_address"] == "10.0.5.254/24"
