from __future__ import annotations

import os
import sys
import tempfile

import pytest


TOKEN = "setup-wizard-api-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_setup_wizard_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
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


def test_setup_wizard_api_routes_are_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/setup-wizard/overview" in routes
    assert "/api/v1/setup-wizard/health" in routes
    assert "/api/v1/setup-wizard/server-readiness" in routes
    assert "/api/v1/setup-wizard/runs" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/state" in routes


def test_setup_wizard_overview_is_read_only_and_arabic(monkeypatch, client):
    from app.api.v1 import setup_wizard

    monkeypatch.setattr(
        setup_wizard,
        "_health_report",
        lambda: {"overall": "healthy", "checks": {}, "checked_at": "2026-06-02T00:00:00Z"},
    )
    monkeypatch.setattr(
        setup_wizard,
        "_server_readiness",
        lambda: {"status": "disabled", "configured": False, "next_action_ar": "الفحص معطل."},
    )

    res = client.get("/api/v1/setup-wizard/overview", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["health"]["overall"] == "healthy"
    assert data["server_readiness"]["status"] == "disabled"
    assert data["safe_operations"]["can_create_run"] is True
    assert data["safe_operations"]["can_apply_router_changes"] is False
    assert data["safe_operations"]["can_apply_server_peer"] is False
    assert "تطبيق إعدادات الراوتر" in data["safe_operations"]["reason_ar"]


def test_setup_wizard_run_can_be_created_and_polled(client):
    created = client.post("/api/v1/setup-wizard/runs", headers=AUTH, json={})
    assert created.status_code == 201, created.get_json()
    run = created.get_json()["data"]["run"]
    assert run["id"] > 0
    assert run["state"] == "COLLECTING"
    assert "radius_secret" not in run
    assert "api_password" not in run

    state = client.get(
        f"/api/v1/setup-wizard/runs/{run['id']}/state",
        headers=AUTH,
    )
    assert state.status_code == 200, state.get_json()
    assert state.get_json()["data"]["run"]["id"] == run["id"]
    assert state.get_json()["data"]["run"]["is_terminal"] is False


def test_setup_wizard_run_state_not_found_is_arabic(client):
    res = client.get("/api/v1/setup-wizard/runs/999/state", headers=AUTH)
    assert res.status_code == 404
    assert res.get_json()["error"]["message"] == "تشغيل معالج الإعداد غير موجود."


def test_setup_wizard_api_requires_token(client):
    res = client.get("/api/v1/setup-wizard/overview")
    assert res.status_code == 401
