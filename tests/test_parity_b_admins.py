"""Parity-b (2026-10-02): admins/roles/account API behave like the web forms.

* A manager editing an admin re-sends the (unchanged) role_id — that is not an
  escalation (web passes None) and must not 403.
* PATCH /roles keeps the role keys the editor does not show (web merge).
* Changing the password through the API clears must_change_password (web does).
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


def _mk(app, perms, pw="pw-12345678"):
    from app.radius.core.tenant import DEFAULT_TENANT_ID, TenantMembership
    from app.radius.db.repos import admins_repo
    from app.radius.stores.tenants_store import TenantsStore
    with app.app_context():
        rn = f"pb_r_{int(time.time() * 1e6)}"
        r = admins_repo.create_role(name=rn, display_name=rn, description="",
                                    permissions=tuple(perms), color="#2BAACC")
        u = f"pb_u_{int(time.time() * 1e6)}"
        a = admins_repo.create_admin(username=u, password=pw, full_name="X", role_id=r.id,
                                     enabled=True, is_super_admin=False)
        TenantsStore.instance().add_membership(TenantMembership(
            id=None, tenant_id=DEFAULT_TENANT_ID, admin_id=a.id, role_id=a.role_id,
            status="active"))
        return a, u, pw


def test_manager_edit_with_unchanged_role_is_not_escalation(app, client):
    _m, mu, mp = _mk(app, ["admins.view", "admins.edit", "users.view"])
    t, _tu, _tp = _mk(app, ["users.view", "settings.view"])
    body = {"full_name": "New", "phone": "0599111222", "role_id": t.role_id, "enabled": True}
    r = client.patch(f"/api/v1/admins/{t.id}", json=body, auth=(mu, mp))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["phone"] == "0599111222"


def test_role_patch_preserves_keys_the_editor_hides(app, client):
    from app.radius.core.constants import EDITABLE_PERMISSIONS
    from app.radius.db.repos import admins_repo
    with app.app_context():
        r = admins_repo.create_role(name=f"pb_rr_{int(time.time() * 1e6)}", display_name="x",
                                    description="", permissions=("users.view", "dashboard.view"),
                                    color="#2BAACC")
    assert "dashboard.view" not in EDITABLE_PERMISSIONS
    res = client.patch(f"/api/v1/roles/{r.id}", json={"permissions": ["users.view"]},
                       auth=("admin", "admin"))
    assert res.status_code == 200, res.get_json()
    assert "dashboard.view" in res.get_json()["data"]["permissions"]


def test_api_password_change_clears_must_change_password(app, client):
    from app.radius.db.repos import admins_repo
    a, u, p = _mk(app, ["users.view"])
    with app.app_context():
        admins_repo.update_admin(a.id, must_change_password=True)
    lr = client.post("/api/admin/login", json={"username": u, "password": p})
    data = (lr.get_json() or {}).get("data") or {}
    tok = data.get("token") or data.get("access_token")
    assert tok, lr.get_json()
    r = client.post("/api/admin/password", json={
        "current_password": p, "new_password": "newpass-123456",
        "confirm_password": "newpass-123456"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        assert admins_repo.get_admin(a.id).must_change_password is False
