"""B-06 (docs/security/BYPASS_PATHS.md): the raw ``is_super_admin`` flag
(«مدير عام» role) must not pick "the first tenant on the server" at login.

Before the fix both login paths (web ``POST /admin/radius/login`` and app
``POST /api/admin/login``) listed **every** tenant for a raw-flag admin and
logged him into ``tenants[0]`` (lowest id) — a tenant he is not a member of.
Only owner-level accounts (``is_owner_like``: licence-panel owner set /
original owner / co-owner) may land in any tenant; everyone else lands in one
of his own active memberships.
"""
from __future__ import annotations

from sec_tenant_helpers import (  # noqa: F401
    PW, app, client, manager, owner, tenants,
)


def _web_login(app, client, username, password=PW):
    app.config["WTF_CSRF_ENABLED"] = False
    r = client.post("/admin/radius/login",
                    data={"username": username, "password": password})
    with client.session_transaction() as s:
        return r, s.get("tenant_id"), s.get("admin_id")


def _app_login(client, username, password=PW):
    r = client.post("/api/admin/login",
                    json={"username": username, "password": password})
    return r, (r.get_json() or {}).get("data", {}).get("tenant_id")


# ───────────────────────── reproducers ─────────────────────────

def test_app_login_raw_flag_lands_in_member_tenant_not_first(app, client):
    _a, b = tenants(app)
    mgr = manager(app, tenant_id=b.id, is_super_admin=True)
    r, tid = _app_login(client, mgr.username)
    assert r.status_code == 200, r.get_json()
    assert tid == b.id, f"raw-flag admin of B landed in tenant {tid}"


def test_web_login_raw_flag_lands_in_member_tenant_not_first(app, client):
    _a, b = tenants(app)
    mgr = manager(app, tenant_id=b.id, is_super_admin=True)
    r, tid, aid = _web_login(app, client, mgr.username)
    assert r.status_code in (302, 303), r.status_code
    assert aid == mgr.id
    assert tid == b.id, f"raw-flag admin of B landed in tenant {tid}"


# ─────────────────── cross-tenant adversarial ───────────────────

def test_two_raw_flag_admins_each_land_in_their_own_tenant(app, client):
    """Same role, same flag, one per tenant: neither crosses over, on either
    login path, and the minted app token is bound to the member tenant."""
    _a, b, c = tenants(app, "tenant-c")
    in_b = manager(app, tenant_id=b.id, is_super_admin=True)
    in_c = manager(app, tenant_id=c.id, is_super_admin=True)
    for mgr, own in ((in_b, b.id), (in_c, c.id)):
        r, tid = _app_login(client, mgr.username)
        assert r.status_code == 200 and tid == own, (mgr.username, tid)
        tok = r.get_json()["data"]["token"]
        from app.radius.db.repos import api_tokens_repo
        with app.app_context():
            rec = api_tokens_repo.resolve_by_plain(tok)
        assert rec and int(rec["tenant_id"]) == own
        client2 = app.test_client()
        r, tid, _aid = _web_login(app, client2, mgr.username)
        assert tid == own, (mgr.username, tid)


def test_raw_flag_member_of_several_tenants_gets_one_of_them(app, client):
    _a, b, c = tenants(app, "tenant-c")
    mgr = manager(app, tenant_id=c.id, is_super_admin=True, extra_tenants=(b.id,))
    _r, tid = _app_login(client, mgr.username)
    assert tid in (b.id, c.id) and tid != 1


# ───────────────────────── unchanged ─────────────────────────

def test_owner_still_lands_on_first_tenant(app, client):
    tenants(app)
    own = owner(app)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.update_admin(own.id, password="owner-pass-9",
                                 must_change_password=0)
    r, tid = _app_login(client, own.username, "owner-pass-9")
    assert r.status_code == 200, r.get_json()
    assert tid == 1
    _r, wtid, _aid = _web_login(app, app.test_client(), own.username, "owner-pass-9")
    assert wtid == 1


def test_co_owner_without_membership_may_land_anywhere(app, client):
    _a, b = tenants(app)
    from app.radius.db.repos import admins_repo
    owner(app)
    with app.app_context():
        co = admins_repo.create_admin(username="co_x", password=PW, full_name="Co")
        admins_repo.update_admin(co.id, is_co_owner=1) if "is_co_owner" in \
            admins_repo.update_admin.__code__.co_varnames else None
        if not admins_repo.is_co_owner(co.id):
            from app.radius.db.connection import transaction
            with transaction() as conn:
                conn.execute("UPDATE admins SET is_co_owner=1 WHERE id=?", (co.id,))
        assert admins_repo.is_primary_owner(co.id)
    r, tid = _app_login(client, "co_x")
    assert r.status_code == 200, r.get_json()
    assert tid == 1      # owner-level → first tenant, unchanged


def test_plain_manager_without_membership_bootstraps_default_unchanged(app, client):
    """Legacy single-tenant bootstrap (no membership → default tenant) is kept."""
    tenants(app)
    owner(app)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.create_admin(username="lonely", password=PW, full_name="L")
    _r, tid = _app_login(client, "lonely")
    assert tid == 1
