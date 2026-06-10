"""Google Drive backup API — real device-flow initiation (not a 501 stub)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_gd_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def test_connect_without_client_returns_needs_configuration(client):
    """No OAuth client saved → clear 409 'بانتظار تفعيلك', not a 501 stub."""
    res = client.post("/api/v1/backups/google-drive/connect", headers=AUTH)
    assert res.status_code == 409
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "needs_configuration"
    assert "بانتظار تفعيلك" in body["error"]["message"]


def test_connect_starts_device_flow_when_configured(app, client, monkeypatch):
    """With the OAuth client configured, connect initiates the real device
    flow and returns the user_code + verification_url."""
    with app.app_context():
        from app.radius.services import google_drive as gd
        gd.save_client(1, "cid.apps.googleusercontent.com", "secret-xyz")
        monkeypatch.setattr(gd, "start_device_flow", lambda tid: {
            "ok": True, "user_code": "ABCD-EFGH",
            "verification_url": "https://www.google.com/device",
            "expires_in": 1800, "interval": 5,
        })

    res = client.post("/api/v1/backups/google-drive/connect", headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["user_code"] == "ABCD-EFGH"
    assert data["verification_url"].endswith("/device")


def test_status_endpoint_reports_disconnected_by_default(client):
    res = client.get("/api/v1/backups/google-drive/status", headers=AUTH)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["connected"] is False
    assert data["configured"] is False
