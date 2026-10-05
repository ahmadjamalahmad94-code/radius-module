"""B-13 (docs/security/BYPASS_PATHS.md): admin accounts are server-global.

``admins`` has no ``tenant_id``; tenancy is ``tenant_memberships``. Before the
fix ``GET /api/v1/admins``, ``GET /api/v1/admins/<id>`` and the web
``/admin/radius/admins`` page returned EVERY admin on the server (username,
email, mobile, last-login IP, role) to a manager of any tenant, and the
per-id endpoints (get/patch/delete, web edit/update/delete) did not check the
target's tenant.

Rule after the fix: a non-owner caller sees and manages only admins with an
active membership in the caller's current tenant — plus, in the default
tenant only, admins with no membership at all (legacy single-tenant accounts
that the login bootstrap attaches to the default tenant). Owner / co-owner and
env (operator) tokens stay server-wide. Admins created by a manager now get a
membership in the creator's tenant, so they stay visible to him (and land
there at first login instead of the default tenant).
"""
from __future__ import annotations

from sec_tenant_helpers import (  # noqa: F401
    app, client, login_web, manager, owner, tenants, token,
)

VIEW = ("admins.view", "admins.edit", "admins.delete", "admins.create",
        "dashboard.view")


def _names(resp):
    return {i.get("username") for i in resp.get_json()["data"]["items"]}


def _setup(app):
    _a, b = tenants(app)
    in_a = manager(app, tenant_id=1, username="victim_in_a")
    in_b = manager(app, tenant_id=b.id, username="peer_in_b")
    mgr_b = manager(app, perms=VIEW, tenant_id=b.id, username="boss_of_b")
    mgr_a = manager(app, perms=VIEW, tenant_id=1, username="boss_of_a")
    return b, in_a, in_b, mgr_b, mgr_a


# ───────────────────────── reproducers ─────────────────────────

def test_api_admin_list_is_scoped_to_callers_tenant(app, client):
    b, in_a, in_b, mgr_b, _mgr_a = _setup(app)
    h = token(app, admin_id=mgr_b.id, scopes=["admin:full"], tenant_id=b.id)
    r = client.get("/api/v1/admins", headers=h)
    assert r.status_code == 200, r.get_json()
    names = _names(r)
    assert in_a.username not in names, "tenant-B manager sees tenant-A admin"
    assert in_b.username in names and mgr_b.username in names


def test_api_admin_get_foreign_is_404(app, client):
    b, in_a, in_b, mgr_b, _mgr_a = _setup(app)
    h = token(app, admin_id=mgr_b.id, scopes=["admin:full"], tenant_id=b.id)
    assert client.get(f"/api/v1/admins/{in_a.id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/admins/{in_b.id}", headers=h).status_code == 200


def test_api_admin_patch_delete_foreign_is_404(app, client):
    b, in_a, _in_b, mgr_b, _mgr_a = _setup(app)
    h = token(app, admin_id=mgr_b.id, scopes=["admin:full"], tenant_id=b.id)
    r = client.patch(f"/api/v1/admins/{in_a.id}", json={"full_name": "pwned"}, headers=h)
    assert r.status_code == 404, r.get_json()
    r = client.delete(f"/api/v1/admins/{in_a.id}", headers=h)
    assert r.status_code == 404, r.get_json()
    from app.radius.db.repos import admins_repo
    with app.app_context():
        a = admins_repo.get_admin(in_a.id)
        assert a is not None and a.full_name != "pwned" and a.deleted_at is None


def test_web_admin_list_is_scoped(app, client):
    b, in_a, in_b, mgr_b, _mgr_a = _setup(app)
    login_web(app, client, mgr_b, b.id)
    r = client.get("/admin/radius/admins")
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert in_a.username not in html, "web /admins leaks tenant-A admin"
    assert in_b.username in html


def test_web_admin_edit_update_delete_foreign_refused(app, client):
    b, in_a, _in_b, mgr_b, _mgr_a = _setup(app)
    app.config["WTF_CSRF_ENABLED"] = False
    login_web(app, client, mgr_b, b.id)
    r = client.get(f"/admin/radius/admins/{in_a.id}/edit")
    assert r.status_code in (302, 403, 404)
    assert in_a.username not in r.get_data(as_text=True)
    client.post(f"/admin/radius/admins/{in_a.id}", data={"full_name": "pwned"})
    client.post(f"/admin/radius/admins/{in_a.id}/delete")
    from app.radius.db.repos import admins_repo
    with app.app_context():
        a = admins_repo.get_admin(in_a.id)
        assert a is not None and a.full_name != "pwned" and a.deleted_at is None


# ─────────────────── cross-tenant, symmetric ───────────────────

def test_each_tenant_manager_sees_only_own_side(app, client):
    b, in_a, in_b, mgr_b, mgr_a = _setup(app)
    ha = token(app, admin_id=mgr_a.id, scopes=["admin:full"], tenant_id=1)
    hb = token(app, admin_id=mgr_b.id, scopes=["admin:full"], tenant_id=b.id)
    na = _names(client.get("/api/v1/admins", headers=ha))
    nb = _names(client.get("/api/v1/admins", headers=hb))
    assert in_a.username in na and in_b.username not in na
    assert in_b.username in nb and in_a.username not in nb
    assert not ({mgr_a.username, in_a.username} & nb)
    # X-Tenant / X-Tenant-Id never widen it
    nb2 = _names(client.get("/api/v1/admins", headers={
        **hb, "X-Tenant": "default", "X-Tenant-Id": "1"}))
    assert nb2 == nb


def test_unbound_token_of_b_is_scoped_too(app, client):
    b, in_a, in_b, _mgr_b, _mgr_a = _setup(app)
    h = token(app, admin_id=0, scopes=["admin:full"], tenant_id=b.id)
    names = _names(client.get("/api/v1/admins", headers=h))
    assert in_a.username not in names and in_b.username in names


def test_admin_created_by_b_manager_stays_in_b(app, client):
    b, _in_a, _in_b, mgr_b, mgr_a = _setup(app)
    hb = token(app, admin_id=mgr_b.id, scopes=["admin:full"], tenant_id=b.id)
    r = client.post("/api/v1/admins", json={"username": "new_in_b",
                                            "password": "pw-123456"}, headers=hb)
    assert r.status_code == 201, r.get_json()
    assert "new_in_b" in _names(client.get("/api/v1/admins", headers=hb))
    ha = token(app, admin_id=mgr_a.id, scopes=["admin:full"], tenant_id=1)
    assert "new_in_b" not in _names(client.get("/api/v1/admins", headers=ha))
    r = client.post("/api/admin/login", json={"username": "new_in_b",
                                              "password": "pw-123456"})
    assert r.status_code == 200 and r.get_json()["data"]["tenant_id"] == b.id


# ───────────────────────── unchanged ─────────────────────────

def test_owner_still_sees_everyone(app, client):
    b, in_a, in_b, _mgr_b, _mgr_a = _setup(app)
    own = owner(app)
    h = token(app, admin_id=own.id, scopes=["admin:full"], tenant_id=1)
    names = _names(client.get("/api/v1/admins", headers=h))
    assert {in_a.username, in_b.username} <= names


def test_default_tenant_still_lists_membershipless_admins(app, client):
    """Single-tenant compatibility: admins without any membership (created
    before this fix / never logged in) remain visible in the default tenant."""
    tenants(app)
    mgr_a = manager(app, perms=VIEW, tenant_id=1)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.create_admin(username="legacy_nomember", password="pw-123456",
                                 full_name="L")
    h = token(app, admin_id=mgr_a.id, scopes=["admin:full"], tenant_id=1)
    assert "legacy_nomember" in _names(client.get("/api/v1/admins", headers=h))
