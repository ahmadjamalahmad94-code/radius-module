from __future__ import annotations

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def test_tools_routes_require_auth(client):
    routes = (
        ("POST", "/api/v1/tools/set-speeds"),
        ("POST", "/api/v1/tools/general-adjustments"),
        ("POST", "/api/v1/tools/test-auth"),
        ("GET", "/api/v1/tools/radius-log"),
        ("POST", "/api/v1/tools/maintenance/preview"),
        ("POST", "/api/v1/tools/maintenance/run"),
    )
    for method, path in routes:
        res = client.open(path, method=method, json={})
        assert res.status_code == 401, path


def test_set_speeds_dry_run_does_not_mutate_plan(client):
    from app.radius.db.repos import plans_repo

    plan = plans_repo.get_plan(1, 1)
    before = (plan.speed_down_kbps, plan.speed_up_kbps)

    res = client.post(
        "/api/v1/tools/set-speeds",
        json={"plan_ids": [1], "set_down": 7777, "set_up": 3333, "dry_run": True},
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["dry_run"] is True
    assert data["matched"] == 1
    assert data["changes"][0]["after"]["speed_down_kbps"] == 7777

    after = plans_repo.get_plan(1, 1)
    assert (after.speed_down_kbps, after.speed_up_kbps) == before


def test_radius_log_api_returns_recent_radpostauth_without_password(client):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas)
            VALUES(?,?,?,?,?,?,?)
            """,
            (1, "tool-user", "secret", "Access-Accept", now_iso(), "ok", "10.0.0.1"),
        )

    res = client.get("/api/v1/tools/radius-log", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    items = res.get_json()["data"]["items"]
    assert any(item["username"] == "tool-user" for item in items)
    assert all("pass" not in item and "password" not in item for item in items)


def test_test_auth_api_returns_policy_decision(client):
    res = client.post(
        "/api/v1/tools/test-auth",
        json={"username": "missing-user", "password": "x"},
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    decision = res.get_json()["data"]["decision"]
    assert decision["ok"] is False
    assert decision["reason"] in {"user_not_found", "engine_error"}


def test_maintenance_requires_preview_token_before_run(client):
    blocked = client.post(
        "/api/v1/tools/maintenance/run",
        json={"action": "vacuum", "days": 1},
        headers=AUTH,
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["error"]["code"] == "confirmation_required"

    preview = client.post(
        "/api/v1/tools/maintenance/preview",
        json={"action": "vacuum", "days": 1},
        headers=AUTH,
    )
    assert preview.status_code == 200, preview.get_json()
    plan = preview.get_json()["data"]
    assert plan["confirm_token"]
    assert plan["confirm_phrase"] == "RUN_MAINTENANCE"

    ran = client.post(
        "/api/v1/tools/maintenance/run",
        json={
            "action": "vacuum",
            "days": 1,
            "confirm_phrase": "RUN_MAINTENANCE",
            "confirm_token": plan["confirm_token"],
        },
        headers=AUTH,
    )
    assert ran.status_code == 200, ran.get_json()
    assert ran.get_json()["data"]["action"] == "vacuum"
