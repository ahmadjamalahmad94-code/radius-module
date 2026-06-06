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


def test_communications_api_manages_channels_and_quota_credit(client):
    channels = client.get("/api/v1/communications/channels", headers=AUTH)
    assert channels.status_code == 200
    channels_data = _data(channels)
    assert channels_data["count"] == 2
    assert {item["channel"] for item in channels_data["items"]} == {"sms", "whatsapp"}

    saved = client.post(
        "/api/v1/communications/channels/sms",
        headers=AUTH,
        json={
            "enabled": True,
            "mode": "admin_quota",
            "send_url_template": "https://gateway.example/send?to={phone}&text={msg}",
            "http_method": "GET",
            "balance_url": "https://gateway.example/balance",
        },
    )
    assert saved.status_code == 200
    channel = _data(saved)["channel"]
    assert channel["channel"] == "sms"
    assert channel["enabled"] is True
    assert channel["active"] is True
    assert channel["mode"] == "admin_quota"
    assert channel["quota"]["is_quota_mode"] is True

    quota_before = client.get("/api/v1/communications/quota", headers=AUTH)
    assert quota_before.status_code == 200
    sms_quota = next(item for item in _data(quota_before)["items"] if item["channel"] == "sms")
    assert sms_quota["balance"] == 0
    assert sms_quota["is_quota_mode"] is True

    credited = client.post(
        "/api/v1/communications/quota/sms/credit",
        headers=AUTH,
        json={"amount": 250, "note": "حزمة اختبار"},
    )
    assert credited.status_code == 201
    credited_data = _data(credited)
    assert credited_data["balance_after"] == 250
    assert credited_data["quota"]["balance"] == 250
    assert credited_data["quota"]["ledger"][0]["delta"] == 250
    assert credited_data["quota"]["ledger"][0]["note"] == "حزمة اختبار"

    from app.radius.db.repos import audit_repo

    audit_rows = audit_repo.recent(1, action="comms_quota_manual_credit")
    assert audit_rows
    assert audit_rows[0]["target_id"] == "sms"


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

    invalid_credit = client.post(
        "/api/v1/communications/quota/sms/credit",
        headers=AUTH,
        json={"amount": 0},
    )
    assert invalid_credit.status_code == 422
