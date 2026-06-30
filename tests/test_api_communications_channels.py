from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


AUTH = {"Authorization": "Bearer communications-api-token"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "communications_channels_api.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "communications-api-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _data(response):
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_communications_api_manages_channels(client):
    channels = client.get("/api/v1/communications/channels", headers=AUTH)
    assert channels.status_code == 200
    channels_data = _data(channels)
    assert channels_data["count"] == 2
    assert {item["channel"] for item in channels_data["items"]} == {"sms", "whatsapp"}

    # SMS & WhatsApp are BYO/self_api now — there is no admin quota model and the
    # channel payload no longer carries a quota block.
    saved = client.post(
        "/api/v1/communications/channels/whatsapp",
        headers=AUTH,
        json={
            "enabled": True,
            "mode": "self_api",
            "send_url_template": "https://gateway.example/send?to={phone}&text={msg}",
            "http_method": "GET",
            "balance_url": "https://gateway.example/balance",
        },
    )
    assert saved.status_code == 200
    channel = _data(saved)["channel"]
    assert channel["channel"] == "whatsapp"
    assert channel["enabled"] is True
    assert channel["active"] is True
    assert channel["mode"] == "self_api"
    assert "quota" not in channel


def test_communications_api_rejects_invalid_channel_settings(client):
    invalid_channel = client.post(
        "/api/v1/communications/channels/fax",
        headers=AUTH,
        json={"enabled": True},
    )
    assert invalid_channel.status_code == 422

    invalid_mode = client.post(
        "/api/v1/communications/channels/sms",
        headers=AUTH,
        json={"mode": "raw"},
    )
    assert invalid_mode.status_code == 422

    # The retired admin_quota mode is now rejected like any other invalid mode.
    rejected_quota_mode = client.post(
        "/api/v1/communications/channels/sms",
        headers=AUTH,
        json={"mode": "admin_quota"},
    )
    assert rejected_quota_mode.status_code == 422
