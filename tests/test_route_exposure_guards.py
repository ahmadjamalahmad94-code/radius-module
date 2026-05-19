"""Route exposure guardrails for S1 dirty-tree stabilization."""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def test_api_admin_auth_routes_are_registered_and_token_guarded(client):
    from app.radius.db.repos import admins_repo

    username = f"route_guard_{uuid4().hex[:12]}"
    password = "route-guard-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Route Guard",
        is_super_admin=True,
    )

    missing = client.get("/api/admin/me")
    assert missing.status_code == 401
    assert missing.get_json()["error"]["code"] == "unauthorized"

    login = client.post(
        "/api/admin/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.get_json()
    token = login.get_json()["data"]["token"]

    me = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.get_json()
    assert me.get_json()["data"]["admin"]["username"] == username


def test_late_admin_route_modules_are_registered_and_login_guarded(app, client):
    expected_rules = {
        "/admin/radius/reports/sessions",
        "/admin/radius/tools/radius_log",
        "/admin/radius/settings",
        "/admin/radius/users/overview",
        "/admin/radius/share_groups",
    }
    actual_rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert expected_rules.issubset(actual_rules)

    for path in sorted(expected_rules):
        res = client.get(path, follow_redirects=False)
        assert res.status_code in {302, 303}, path
        assert "/admin/radius/login" in res.headers.get("Location", "")
