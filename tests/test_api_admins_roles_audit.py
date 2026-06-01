"""
Slice D regression tests — admins, roles, permissions catalog, audit log.

Same /api/v1/admin/login flow; tests rely on the seeded default admin
(`admin/admin`) and the seeded system roles (super_admin / operator /
support / billing / viewer).
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


# ─────────────── permissions catalog ───────────────

def test_permissions_catalog_lists_known_perms(client, auth_headers):
    res = client.get("/api/v1/permissions", headers=auth_headers)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["count"] > 0
    # Spot-check a few known ones
    assert "users.view" in data["items"]
    assert "admins.delete" in data["items"]
    assert "audit.view" in data["items"]
    # Groups have labels
    assert any(g["key"] == "users" for g in data["groups"])


# ─────────────── admins ───────────────

def test_admins_list_includes_seed(client, auth_headers):
    res = client.get("/api/v1/admins", headers=auth_headers)
    assert res.status_code == 200
    items = res.get_json()["data"]["items"]
    usernames = [a["username"] for a in items]
    assert "admin" in usernames
    # password_hash never leaks
    for a in items:
        assert "password_hash" not in a
        assert "password" not in a


def test_admin_create_patch_delete_lifecycle(client, auth_headers):
    username = f"qa_admin_{int(time.time() * 1000)}"
    # CREATE
    res = client.post(
        "/api/v1/admins",
        json={
            "username": username,
            "password": "pw1234",
            "full_name": "QA Admin",
            "email": "qa@example.com",
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert res.status_code == 201, res.get_json()
    admin = res.get_json()["data"]
    assert admin["username"] == username
    assert "password_hash" not in admin
    aid = admin["id"]

    # GET
    res = client.get(f"/api/v1/admins/{aid}", headers=auth_headers)
    assert res.status_code == 200
    assert res.get_json()["data"]["full_name"] == "QA Admin"

    # PATCH name
    res = client.patch(
        f"/api/v1/admins/{aid}",
        json={"full_name": "QA Admin Patched", "enabled": False},
        headers=auth_headers,
    )
    assert res.status_code == 200
    d = res.get_json()["data"]
    assert d["full_name"] == "QA Admin Patched"
    assert d["enabled"] is False

    # PATCH password — must succeed without leaking
    res = client.patch(
        f"/api/v1/admins/{aid}",
        json={"password": "rotated_pw"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert "password" not in res.get_json()["data"]
    assert "password_hash" not in res.get_json()["data"]

    # DELETE
    res = client.delete(f"/api/v1/admins/{aid}", headers=auth_headers)
    assert res.status_code == 200

    # GET after delete → 404
    res = client.get(f"/api/v1/admins/{aid}", headers=auth_headers)
    assert res.status_code == 404


def test_admin_create_missing_username_returns_422(client, auth_headers):
    res = client.post(
        "/api/v1/admins",
        json={"password": "pw"},
        headers=auth_headers,
    )
    assert res.status_code == 422


def test_admin_create_missing_password_returns_422(client, auth_headers):
    res = client.post(
        "/api/v1/admins",
        json={"username": "x"},
        headers=auth_headers,
    )
    assert res.status_code == 422


def test_admin_create_duplicate_username_returns_409(client, auth_headers):
    res = client.post(
        "/api/v1/admins",
        json={"username": "admin", "password": "pw1234"},
        headers=auth_headers,
    )
    assert res.status_code == 409


def test_admin_delete_super_admin_forbidden(client, auth_headers):
    # The seeded `admin` is super_admin; deleting must return 403, not 200.
    admins = client.get("/api/v1/admins", headers=auth_headers).get_json()["data"]["items"]
    super_a = next(a for a in admins if a["username"] == "admin")
    res = client.delete(f"/api/v1/admins/{super_a['id']}", headers=auth_headers)
    assert res.status_code == 403


# ─────────────── roles ───────────────

def test_roles_list_includes_system_roles(client, auth_headers):
    res = client.get("/api/v1/roles", headers=auth_headers)
    assert res.status_code == 200
    items = res.get_json()["data"]["items"]
    names = [r["name"] for r in items]
    assert "super_admin" in names
    assert "viewer" in names
    super_role = next(r for r in items if r["name"] == "super_admin")
    assert super_role["is_system"] is True
    assert "users.view" in super_role["permissions"]


def test_role_create_patch_delete_lifecycle(client, auth_headers):
    name = f"qa_role_{int(time.time() * 1000)}"
    # CREATE
    res = client.post(
        "/api/v1/roles",
        json={
            "name": name,
            "display_name": "QA Role",
            "description": "from API tests",
            "permissions": ["users.view", "plans.view"],
            "color": "#FF00FF",
        },
        headers=auth_headers,
    )
    assert res.status_code == 201, res.get_json()
    role = res.get_json()["data"]
    rid = role["id"]
    assert role["is_system"] is False
    assert role["permissions"] == ["users.view", "plans.view"]

    # PATCH permissions
    res = client.patch(
        f"/api/v1/roles/{rid}",
        json={"permissions": ["users.view", "plans.view", "cards.view"]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert "cards.view" in res.get_json()["data"]["permissions"]

    # DELETE
    res = client.delete(f"/api/v1/roles/{rid}", headers=auth_headers)
    assert res.status_code == 200


def test_role_create_with_unknown_permission_returns_422(client, auth_headers):
    res = client.post(
        "/api/v1/roles",
        json={"name": "qa_bad_perms", "permissions": ["users.view", "ALL_THE_THINGS"]},
        headers=auth_headers,
    )
    assert res.status_code == 422
    assert "ALL_THE_THINGS" in res.get_json()["error"]["message"]


def test_role_delete_system_role_forbidden(client, auth_headers):
    roles = client.get("/api/v1/roles", headers=auth_headers).get_json()["data"]["items"]
    sys_role = next(r for r in roles if r["is_system"])
    res = client.delete(f"/api/v1/roles/{sys_role['id']}", headers=auth_headers)
    assert res.status_code == 403


# ─────────────── audit ───────────────

def test_audit_list_returns_recent_events(client, auth_headers):
    # Generate an audit event by creating an admin
    name = f"qa_audit_actor_{int(time.time() * 1000)}"
    create = client.post(
        "/api/v1/admins",
        json={"username": name, "password": "pw1234"},
        headers=auth_headers,
    )
    aid = create.get_json()["data"]["id"]

    try:
        res = client.get("/api/v1/audit?limit=50", headers=auth_headers)
        assert res.status_code == 200
        items = res.get_json()["data"]["items"]
        assert items, "audit should contain at least the create event"
        # Most recent should be the create we just did
        recent = items[0]
        assert "actor" in recent
        assert "action" in recent
        assert "target_type" in recent

        # Filter by target_type=admin
        res = client.get(
            "/api/v1/audit?target_type=admin&limit=20",
            headers=auth_headers,
        )
        filtered = res.get_json()["data"]["items"]
        assert all(it["target_type"] == "admin" for it in filtered)

        bad_limit = client.get("/api/v1/audit?limit=abc", headers=auth_headers)
        assert bad_limit.status_code == 422
        assert bad_limit.get_json()["error"]["message"] == "قيمة limit يجب أن تكون رقمًا صحيحًا."
        assert "limit must be int" not in bad_limit.get_json()["error"]["message"]
    finally:
        client.delete(f"/api/v1/admins/{aid}", headers=auth_headers)
