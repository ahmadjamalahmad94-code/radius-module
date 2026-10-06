"""Security — the owner's X-Tenant / token-scope decisions (2026-10-06).

(a) A read-scope token can never call a mutating method (POST/PUT/PATCH/DELETE),
    whatever the read-scope spelling, even for the owner.
(b) An UNBOUND DB token (created_by = 0) needs an explicit scope: empty,
    unknown or permission-like scopes are refused — no implicit full access.
(c) HTTP Basic: an admin may select (X-Tenant-Id) only one of HIS OWN tenants
    with an ACTIVE membership in a usable (not suspended / closed) tenant; a
    raw ``is_super_admin`` flag or the header alone never bypasses membership.
    Only the platform owner / co-owners roam. Web ``X-Tenant`` follows the
    same rule.

See docs/security/SEC_XTENANT_DECISIONS.md.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_xtd_")
    db_file = os.path.join(tmp, "t.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_AUTH_REQUIRED", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    a = create_app()
    a.testing = True
    seen: dict = {}

    @a.teardown_request
    def _capture(_exc=None):  # noqa: ANN001
        from flask import g, request
        seen[request.path] = getattr(g, "tenant_id", None)

    a.config["_SEEN_TENANT"] = seen
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


# ───────────────────────────── helpers ─────────────────────────────

def _owner(app):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        pid = admins_repo.primary_admin_id()
        if pid is None:
            admins_repo.create_admin(username="owner_x", password="owner-pass",
                                     full_name="Owner", is_super_admin=True)
            pid = admins_repo.primary_admin_id()
        return admins_repo.get_admin(pid)


def _tenants(app):
    """Tenant A (default, id 1) and tenant B (slug ``tenant-b``)."""
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        tenants_repo.ensure_default_tenant()
        a = tenants_repo.get_tenant(1)
        b = tenants_repo.get_by_slug("tenant-b") or tenants_repo.create_tenant(
            Tenant(id=None, slug="tenant-b", name="Tenant B"))
    return a, b


def _manager(app, perms=("dashboard.view",), *, tenant_id=1, is_super_admin=False):
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    _owner(app)
    with app.app_context():
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}",
                                       display_name="r", permissions=tuple(perms))
        u = f"m_{uuid4().hex[:8]}"
        adm = admins_repo.create_admin(username=u, password="pw-123456",
                                       full_name="M", is_super_admin=is_super_admin,
                                       role_id=role.id)
        tenants_repo.add_membership(TenantMembership(
            id=None, tenant_id=tenant_id, admin_id=adm.id, role_id=role.id))
    return adm


def _token(app, *, admin_id, scopes, tenant_id=1):
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=tenant_id, name=f"t{uuid4().hex[:6]}", scopes=list(scopes),
            created_by=int(admin_id))
    return {"Authorization": f"Bearer {plain}"}


def _login_web(app, client, admin, tenant_id):
    from app.radius.auth.session_helpers import set_current_admin
    with app.test_request_context("/"):
        from flask import session
        set_current_admin(admin, tenant_id)
        data = dict(session)
    with client.session_transaction() as s:
        s.update(data)


def _seen(app, path):
    return app.config["_SEEN_TENANT"].get(path)


def _basic(username, password):
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def _join(app, admin_id, tenant_id, status="active"):
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        tenants_repo.add_membership(TenantMembership(
            id=None, tenant_id=tenant_id, admin_id=admin_id, status=status))


def _set_tenant_status(app, tenant_id, status):
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        tenants_repo.update_tenant(tenant_id, status=status)


def _owner_basic(app):
    from app.radius.db.repos import admins_repo
    owner = _owner(app)
    with app.app_context():
        admins_repo.update_admin(owner.id, password="owner-pass-9",
                                 must_change_password=0)
    return owner, _basic(owner.username, "owner-pass-9")


def _bare_admin(app, *, is_super_admin):
    """An admin with NO tenant membership at all."""
    from app.radius.db.repos import admins_repo
    _owner(app)
    with app.app_context():
        u = f"n_{uuid4().hex[:8]}"
        return admins_repo.create_admin(username=u, password="pw-123456",
                                        full_name="N", is_super_admin=is_super_admin)


# ───────────── (a) read-scope tokens: no mutating method ─────────────

@pytest.mark.parametrize("scope", ["read", "readonly", "read-only", "read_only",
                                   "api:read", "admin:read", "READ"])
def test_a_every_read_scope_spelling_blocks_post(app, client, scope):
    owner = _owner(app)
    h = _token(app, admin_id=owner.id, scopes=[scope])
    r = client.post("/api/v1/tokens", json={"name": "x", "scopes": ["read"]}, headers=h)
    assert r.status_code == 403, (scope, r.status_code, r.get_json())
    assert (r.get_json()["error"].get("details") or {}).get("reason") == "token_scope"


@pytest.mark.parametrize("method", ["PATCH", "PUT", "DELETE"])
def test_a_read_scope_blocks_patch_put_delete(app, client, method):
    owner = _owner(app)
    victim = _manager(app)
    h = _token(app, admin_id=owner.id, scopes=["read"])
    r = client.open(f"/api/v1/admins/{victim.id}", method=method,
                    json={"full_name": "pwned"}, headers=h)
    # PUT has no route (405 is fine) — never 2xx.
    assert r.status_code in (403, 405), (method, r.status_code, r.get_json())
    if method != "PUT":
        assert r.status_code == 403
    from app.radius.db.repos import admins_repo
    with app.app_context():
        a = admins_repo.get_admin(victim.id)
        assert a is not None and a.full_name != "pwned"


def test_a_read_plus_permission_like_scope_is_still_read_only(app, client):
    owner = _owner(app)
    h = _token(app, admin_id=owner.id, scopes=["read", "cards.view"])
    r = client.post("/api/v1/tokens", json={"name": "x"}, headers=h)
    assert r.status_code == 403


def test_a_read_scope_cannot_revoke_tokens(app, client):
    owner = _owner(app)
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        rec, _p = api_tokens_repo.create_token(tenant_id=1, name="keep",
                                               scopes=["read"], created_by=owner.id)
    h = _token(app, admin_id=owner.id, scopes=["read"])
    r = client.post(f"/api/v1/tokens/{rec['id']}/revoke", headers=h)
    assert r.status_code == 403, r.get_json()


# ───────────── (b) unbound tokens need an explicit scope ─────────────

@pytest.mark.parametrize("scopes", [[], ["cards.view"], ["dashboard.view"],
                                    ["unknown-scope"], ["  "]])
def test_b_unbound_token_without_explicit_scope_is_refused(app, client, scopes):
    _owner(app)
    h = _token(app, admin_id=0, scopes=scopes)
    for path in ("/api/v1/dashboard", "/api/v1/tokens"):
        r = client.get(path, headers=h)
        assert r.status_code == 403, (scopes, path, r.status_code, r.get_json())


def test_b_unbound_read_token_reads_but_never_writes(app, client):
    _owner(app)
    h = _token(app, admin_id=0, scopes=["read"])
    assert client.get("/api/v1/dashboard", headers=h).status_code == 200
    assert client.post("/api/v1/notifications/read-all", headers=h).status_code == 403


# ───────────── (c) HTTP Basic tenant selection ─────────────

def _dash(client, mgr, tenant_id=None, password="pw-123456"):
    h = _basic(mgr.username, password)
    if tenant_id is not None:
        h["X-Tenant-Id"] = str(tenant_id)
    return client.get("/api/v1/dashboard", headers=h)


def test_c_basic_member_may_select_his_second_active_tenant(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id)
    r = _dash(client, mgr, b.id)
    assert r.status_code == 200, r.get_json()
    assert _seen(app, "/api/v1/dashboard") == b.id


def test_c_basic_non_member_selecting_foreign_tenant_is_refused(app, client):
    """Plain manager (no flag) of A asks for B: refused outright (fail
    closed), not silently re-routed to A."""
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    r = _dash(client, mgr, b.id)
    assert r.status_code == 403, (r.status_code, r.get_json())
    assert _seen(app, "/api/v1/dashboard") != b.id


def test_c_basic_raw_super_flag_non_member_refused(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1, is_super_admin=True)
    r = _dash(client, mgr, b.id)
    assert r.status_code == 403, (r.status_code, r.get_json())


def test_c_basic_raw_super_flag_without_any_membership_gets_no_tenant(app, client):
    """The raw flag used to land a membership-less admin on the default tenant
    over Basic — the flag must not stand in for a membership."""
    _tenants(app)
    adm = _bare_admin(app, is_super_admin=True)
    r = _dash(client, adm)
    assert r.status_code in (401, 403), (r.status_code, r.get_json())


def test_c_basic_member_cannot_select_suspended_tenant(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id)
    _set_tenant_status(app, b.id, "suspended")
    r = _dash(client, mgr, b.id)
    assert r.status_code == 403, (r.status_code, r.get_json())
    assert _seen(app, "/api/v1/dashboard") != b.id


def test_c_basic_member_cannot_select_closed_tenant(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id)
    _set_tenant_status(app, b.id, "closed")
    assert _dash(client, mgr, b.id).status_code == 403


def test_c_basic_default_pick_skips_suspended_tenant(app, client):
    """No header: the admin lands in an active tenant of his, never in a
    suspended one even if it has the lowest id."""
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id)
    _set_tenant_status(app, 1, "suspended")
    r = _dash(client, mgr)
    assert r.status_code == 200, r.get_json()
    assert _seen(app, "/api/v1/dashboard") == b.id


def test_c_basic_only_suspended_tenants_means_no_access(app, client):
    _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _set_tenant_status(app, 1, "suspended")
    assert _dash(client, mgr).status_code in (401, 403)


def test_c_basic_inactive_membership_cannot_be_selected(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id, status="removed")
    r = _dash(client, mgr, b.id)
    assert r.status_code == 403
    assert _seen(app, "/api/v1/dashboard") != b.id


def test_c_basic_trial_tenant_is_selectable(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id)
    _set_tenant_status(app, b.id, "trial")
    r = _dash(client, mgr, b.id)
    assert r.status_code == 200
    assert _seen(app, "/api/v1/dashboard") == b.id


def test_c_basic_non_numeric_header_is_refused(app, client):
    _tenants(app)
    mgr = _manager(app, tenant_id=1)
    assert _dash(client, mgr, "tenant-b").status_code == 403


def test_c_owner_roams_even_into_suspended_tenant(app, client):
    _a, b = _tenants(app)
    _set_tenant_status(app, b.id, "suspended")
    _owner_obj, h = _owner_basic(app)
    r = client.get("/api/v1/dashboard", headers={**h, "X-Tenant-Id": str(b.id)})
    assert r.status_code == 200, r.get_json()
    assert _seen(app, "/api/v1/dashboard") == b.id


def test_c_co_owner_roams(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.set_co_owner(mgr.id, True)
    r = _dash(client, mgr, b.id)
    assert r.status_code == 200, r.get_json()
    assert _seen(app, "/api/v1/dashboard") == b.id


def test_c_cross_tenant_write_via_header_is_refused(app, client):
    """Cross-tenant: an A-only manager allowed to create admins cannot create
    one inside B by naming B in X-Tenant-Id — refused, nothing written."""
    _a, b = _tenants(app)
    mgr = _manager(app, perms=("dashboard.view", "admins.view", "admins.create"),
                   tenant_id=1)
    uname = f"x_{uuid4().hex[:6]}"
    h = {**_basic(mgr.username, "pw-123456"), "X-Tenant-Id": str(b.id)}
    r = client.post("/api/v1/admins",
                    json={"username": uname, "password": "Pw-123456789", "full_name": "X"},
                    headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())
    from app.radius.db.repos import admins_repo
    with app.app_context():
        assert admins_repo.get_by_username(uname) is None


def test_c_cross_tenant_read_via_header_is_refused(app, client):
    """An A-only manager cannot read B's admin list by header."""
    _a, b = _tenants(app)
    mgr = _manager(app, perms=("dashboard.view", "admins.view"), tenant_id=1)
    other = _manager(app, tenant_id=b.id)
    h = {**_basic(mgr.username, "pw-123456"), "X-Tenant-Id": str(b.id)}
    r = client.get("/api/v1/admins", headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())
    assert other.username not in (r.get_data(as_text=True) or "")


# ───────────── web X-Tenant: same rule ─────────────

def test_web_member_cannot_switch_into_suspended_tenant(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _join(app, mgr.id, b.id)
    _set_tenant_status(app, b.id, "suspended")
    _login_web(app, client, mgr, 1)
    client.get("/", headers={"X-Tenant": b.slug})
    assert _seen(app, "/") == 1


def test_web_raw_super_flag_non_member_cannot_switch(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1, is_super_admin=True)
    _login_web(app, client, mgr, 1)
    client.get("/", headers={"X-Tenant": b.slug})
    assert _seen(app, "/") == 1


def test_web_owner_roams_into_suspended_tenant(app, client):
    _a, b = _tenants(app)
    _set_tenant_status(app, b.id, "suspended")
    owner = _owner(app)
    _login_web(app, client, owner, 1)
    client.get("/", headers={"X-Tenant": b.slug})
    assert _seen(app, "/") == b.id


def test_c_temp_password_admin_gets_password_change_code_before_tenant_refusal(app, client):
    """Interaction with SEC temp-password: a Basic caller still on a temporary
    password who names a tenant he cannot select is refused either way, but
    with the actionable PASSWORD_CHANGE_REQUIRED code (password was verified)."""
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.update_admin(mgr.id, must_change_password=1)
    r = _dash(client, mgr, b.id)
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert _seen(app, "/api/v1/dashboard") != b.id


def test_c_wrong_password_with_foreign_header_is_401_not_403(app, client):
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    r = _dash(client, mgr, b.id, password="wrong-pass")
    assert r.status_code == 401
