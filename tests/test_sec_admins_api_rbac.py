"""SEC H1 — /api/v1/admins is super-only.

The admin/role management API was reachable by ANY authenticated principal.
Worse, HTTP Basic auth grants every admin the "admin:full" scope regardless of
their real super status (auth.py), so a plain operator could:

  * list the entire admin roster,
  * create a NEW admin with is_super_admin=True (privilege escalation),
  * delete/patch other admins, rewrite role permission sets.

The fix resolves the bound principal and requires is_super_admin / primary
owner — the "admin:full" scope alone is no longer trusted for account
management.
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


def _make_non_super_admin(app):
    """Create an enabled, NON-super admin WITH a tenant membership (so Basic
    auth resolves a tenant and reaches the RBAC gate rather than 401-ing on a
    missing membership)."""
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.core.tenant import TenantMembership, DEFAULT_TENANT_ID
        from app.radius.stores.tenants_store import TenantsStore
        u = f"lowpriv_{int(time.time() * 1000)}"
        admin = admins_repo.create_admin(
            username=u, password="low-pass", full_name="Low Priv",
            is_super_admin=False, enabled=True,
        )
        TenantsStore.instance().add_membership(TenantMembership(
            id=None, tenant_id=DEFAULT_TENANT_ID, admin_id=admin.id,
            role_id=admin.role_id, status="active"))
        return u, "low-pass"


# ─── the owner keeps full access (back-compat) ───

def test_primary_owner_can_list_admins(client):
    res = client.get("/api/v1/admins", auth=("admin", "admin"))
    assert res.status_code == 200, res.get_json()


# ─── a non-super admin is refused across the surface ───

def test_non_super_cannot_list_admins(app, client):
    u, p = _make_non_super_admin(app)
    res = client.get("/api/v1/admins", auth=(u, p))
    assert res.status_code == 403, res.get_json()
    assert res.get_json()["error"]["code"] == "forbidden"


def test_non_super_cannot_mint_super_admin(app, client):
    """The escalation path: a low-priv operator forging a super account."""
    u, p = _make_non_super_admin(app)
    res = client.post(
        "/api/v1/admins",
        json={"username": f"forged_{int(time.time()*1000)}",
              "password": "x", "is_super_admin": True},
        auth=(u, p),
    )
    assert res.status_code == 403, res.get_json()


def test_non_super_cannot_patch_or_delete_admin(app, client):
    u, p = _make_non_super_admin(app)
    patch = client.patch("/api/v1/admins/1", json={"is_super_admin": True}, auth=(u, p))
    assert patch.status_code == 403, patch.get_json()
    delete = client.delete("/api/v1/admins/1", auth=(u, p))
    assert delete.status_code == 403, delete.get_json()


def test_non_super_cannot_mutate_roles(app, client):
    u, p = _make_non_super_admin(app)
    res = client.post("/api/v1/roles", json={"name": "sneaky", "permissions": []}, auth=(u, p))
    assert res.status_code == 403, res.get_json()


# ─── the gate helper itself ───

def test_gate_rejects_bound_non_super(app):
    from app.api.v1.admins import _can_manage_admins
    from app.radius.db.repos import admins_repo
    username, _pw = _make_non_super_admin(app)
    with app.test_request_context("/"):
        from flask import g
        target = admins_repo.get_by_username(username)
        # Simulate the resolved auth context of a bound non-super principal.
        g.admin_id = target.id
        g.api_token_scopes = ["admin:full"]  # scope present but must NOT suffice
        assert _can_manage_admins() is False


def test_gate_allows_unbound_master_token(app):
    from app.api.v1.admins import _can_manage_admins
    with app.test_request_context("/"):
        from flask import g
        g.admin_id = 0            # env master token — no admin binding
        g.api_token_scopes = ["admin:full"]
        assert _can_manage_admins() is True
