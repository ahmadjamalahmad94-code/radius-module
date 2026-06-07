from __future__ import annotations

import os
import sys
import tempfile

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_whatsapp_api_")
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


def test_whatsapp_api_routes_are_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/whatsapp" in routes
    assert "/api/v1/whatsapp/settings" in routes
    assert "/api/v1/whatsapp/test" in routes
    assert "/api/v1/whatsapp/cloud-test" in routes


def test_whatsapp_state_normalizes_panel_status(client, monkeypatch):
    from app.radius.services import admin_panel_client

    class FakePanel:
        def get_whatsapp_status(self):
            return {
                "ok": True,
                "status": "success",
                "response": {
                    "enabled": True,
                    "account_status": "connected",
                    "onboarding_state": "connected",
                    "display_phone_number": "+970599000000",
                    "business_display_name": "Hobe Radius",
                    "usage": {"sent": 7, "remaining": 93, "limit": 100},
                },
            }

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)

    res = client.get("/api/v1/whatsapp", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["status"]["connected"] is True
    assert data["status"]["onboarding_label"] == "متصل"
    assert data["status"]["phone"] == "+970599000000"
    assert data["events"][0]["setting_key"] == "whatsapp.send.otp"
    assert data["events"][0]["enabled"] is False
    assert "provider" not in str(data).lower()
    assert "token" not in str(data).lower()


def test_whatsapp_settings_save_updates_event_gates(app, client):
    res = client.patch(
        "/api/v1/whatsapp/settings",
        headers=AUTH,
        json={"toggles": {"otp": True, "expiry": "1", "quota": False}},
    )
    assert res.status_code == 200, res.get_json()
    events = {item["key"]: item for item in res.get_json()["data"]["events"]}
    assert events["otp"]["enabled"] is True
    assert events["expiry"]["enabled"] is True
    assert events["quota"]["enabled"] is False

    with app.app_context():
        from app.radius.db.repos import tenants_repo

        assert tenants_repo.get_setting(1, "whatsapp.send.otp") == "1"
        assert tenants_repo.get_setting(1, "whatsapp.send.quota") == "0"


def test_whatsapp_settings_reject_unknown_event(client):
    res = client.patch(
        "/api/v1/whatsapp/settings",
        headers=AUTH,
        json={"toggles": {"otp": True, "raw_secret": True}},
    )
    assert res.status_code == 422, res.get_json()
    assert res.get_json()["error"]["message"] == "يوجد نوع رسالة غير معروف."


def test_whatsapp_test_uses_panel_bridge(client, monkeypatch):
    from app.radius.services import admin_panel_client

    calls = {}

    class FakePanel:
        def send_whatsapp_test(self, **kwargs):
            calls["test"] = kwargs
            return {"ok": True, "status": "sent"}

        def send_whatsapp_cloud_test(self, **kwargs):
            calls["cloud"] = kwargs
            return {"ok": True, "response": {"ok": True}}

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)

    test_res = client.post(
        "/api/v1/whatsapp/test",
        headers=AUTH,
        json={"recipient_phone": "+970599000000"},
    )
    assert test_res.status_code == 200, test_res.get_json()
    assert calls["test"]["recipient_phone"] == "+970599000000"
    assert calls["test"]["idempotency_key"].startswith("wa-test-1-")

    cloud_res = client.post(
        "/api/v1/whatsapp/cloud-test",
        headers=AUTH,
        json={
            "recipient_phone": "+970599000000",
            "template_name": "hello_world",
            "language": "ar",
        },
    )
    assert cloud_res.status_code == 200, cloud_res.get_json()
    assert calls["cloud"]["template_name"] == "hello_world"
