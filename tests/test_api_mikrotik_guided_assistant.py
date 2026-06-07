"""API contract for the MikroTik guided operation assistant."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_api_o12_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    with created.app_context():
        from app.radius.db.connection import transaction

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                VALUES (1, 1, 'core-router', '203.0.113.20', 'radius-secret',
                        'mikrotik', 'hotspot', 1, ?, 'direct',
                        'admin', 'router-secret')
                """,
                (now,),
            )
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def test_guided_assistant_route_is_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)

    assert res.status_code == 200
    rules = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/mikrotik/<int:nas_id>/assistant" in rules


def test_guided_assistant_requires_api_auth(client):
    res = client.get("/api/v1/mikrotik/1/assistant")

    assert res.status_code == 401


def test_guided_assistant_returns_checklist_contract(client):
    res = client.get(
        "/api/v1/mikrotik/1/assistant?op=programming_hotspot",
        headers=AUTH,
    )

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["nas_id"] == 1
    assert data["operation"] == "programming_hotspot"
    assert data["operation_label_ar"]
    assert isinstance(data["can_proceed"], bool)
    assert data["apply_href"].startswith("/admin/radius/mt/1/")
    assert data["blocking_count"] >= 0
    assert data["warning_count"] >= 0
    assert {step["key"] for step in data["steps"]} == {
        "health",
        "safety",
        "backup",
        "recent_failure",
        "apply_link",
    }
    assert data["operation_choices"][0]["code"] == "programming_hotspot"
    assert "router-secret" not in res.get_data(as_text=True)
    assert "radius-secret" not in res.get_data(as_text=True)


def test_guided_assistant_unknown_operation_uses_web_default(client):
    res = client.get(
        "/api/v1/mikrotik/1/assistant?op=not-real",
        headers=AUTH,
    )

    assert res.status_code == 200
    assert res.get_json()["data"]["operation"] == "programming_hotspot"


def test_guided_assistant_returns_404_for_unknown_router(client):
    res = client.get("/api/v1/mikrotik/999/assistant", headers=AUTH)

    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"
