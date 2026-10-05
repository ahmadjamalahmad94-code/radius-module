"""Security: tenant override headers + API token scopes.

B. ``X-Tenant`` (slug, app/radius/middleware/tenant_resolver.py) and
   ``X-Tenant-Id`` (HTTP Basic, app/api/auth.py) must never move an
   authenticated admin into a tenant he is not a member of — only the
   owner / co-owner may pick any tenant. Anonymous requests (public store,
   which verifies a per-tenant store key) keep the slug resolution.

A. API token scopes: a token whose scopes are read-only cannot call a
   mutating method (scopes only ever NARROW the bound admin's permissions —
   the permission guard still runs on the admin). Unbound DB tokens
   (``created_by`` = 0) keep their unscoped behaviour only when explicitly
   flagged ``admin:full`` / ``*``; env tokens are unchanged.
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
    tmp = tempfile.mkdtemp(prefix="hr_scope_tenant_")
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


# ─────────────────── B. tenant override headers ───────────────────

def test_web_x_tenant_cannot_switch_non_member_into_foreign_tenant(app, client):
    """A tenant-A manager sends ``X-Tenant: tenant-b`` on a web page: the
    request must stay in tenant A (the topbar switcher refuses non-members;
    the header must not be a way around it)."""
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    _login_web(app, client, mgr, 1)
    client.get("/", headers={"X-Tenant": b.slug})
    assert _seen(app, "/") == 1, "X-Tenant moved a non-member into tenant B"


def test_web_x_tenant_still_works_for_member(app, client):
    _a, b = _tenants(app)
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import tenants_repo
    mgr = _manager(app, tenant_id=1)
    with app.app_context():
        tenants_repo.add_membership(TenantMembership(
            id=None, tenant_id=b.id, admin_id=mgr.id))
    _login_web(app, client, mgr, 1)
    client.get("/", headers={"X-Tenant": b.slug})
    assert _seen(app, "/") == b.id


def test_web_x_tenant_owner_may_pick_any_tenant(app, client):
    _a, b = _tenants(app)
    owner = _owner(app)
    _login_web(app, client, owner, 1)
    client.get("/", headers={"X-Tenant": b.slug})
    assert _seen(app, "/") == b.id


def test_api_bearer_ignores_x_tenant(app, client):
    """API: a tenant-A token with ``X-Tenant: tenant-b`` stays in tenant A
    (enforce_api_auth overwrites g.tenant_id with the token tenant)."""
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1)
    h = _token(app, admin_id=mgr.id, scopes=["admin:full"])
    r = client.get("/api/v1/dashboard", headers={**h, "X-Tenant": b.slug})
    assert r.status_code == 200, r.get_json()
    assert _seen(app, "/api/v1/dashboard") == 1


def test_api_basic_x_tenant_id_needs_membership_not_raw_super_flag(app, client):
    """HTTP Basic: a NON-owner admin carrying the raw ``is_super_admin`` flag
    must not pick a foreign tenant through ``X-Tenant-Id``."""
    _a, b = _tenants(app)
    mgr = _manager(app, tenant_id=1, is_super_admin=True)
    raw = base64.b64encode(f"{mgr.username}:pw-123456".encode()).decode()
    client.get("/api/v1/dashboard",
               headers={"Authorization": f"Basic {raw}", "X-Tenant-Id": str(b.id)})
    assert _seen(app, "/api/v1/dashboard") != b.id, \
        "X-Tenant-Id moved a non-owner, non-member admin into tenant B"


def test_api_basic_x_tenant_id_owner_may_pick(app, client):
    _a, b = _tenants(app)
    owner = _owner(app)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.update_admin(owner.id, password="owner-pass-9",
                                 must_change_password=0)
    raw = base64.b64encode(f"{owner.username}:owner-pass-9".encode()).decode()
    r = client.get("/api/v1/dashboard",
                   headers={"Authorization": f"Basic {raw}", "X-Tenant-Id": str(b.id)})
    assert r.status_code == 200, r.get_json()
    assert _seen(app, "/api/v1/dashboard") == b.id


def test_anonymous_store_keeps_slug_resolution(app, client):
    """No admin session → the slug still selects the tenant (the public store
    verifies its per-tenant store key against it)."""
    _a, b = _tenants(app)
    client.get("/api/v1/store/ping", headers={"X-Tenant": b.slug})
    assert _seen(app, "/api/v1/store/ping") == b.id


# ─────────────────────── A. token scopes ───────────────────────

def test_read_scope_token_cannot_write_even_for_owner(app, client):
    owner = _owner(app)
    h = _token(app, admin_id=owner.id, scopes=["read"])
    r = client.post("/api/v1/tokens", json={"name": "escalate", "scopes": ["read"]},
                    headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())
    assert (r.get_json() or {}).get("error", {}).get("code") == "forbidden"


def test_read_scope_token_can_still_read(app, client):
    owner = _owner(app)
    h = _token(app, admin_id=owner.id, scopes=["read"])
    assert client.get("/api/v1/tokens", headers=h).status_code == 200


def test_read_scope_token_blocked_on_auth_only_write(app, client):
    owner = _owner(app)
    h = _token(app, admin_id=owner.id, scopes=["read"])
    r = client.post("/api/v1/notifications/read-all", headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())


def test_full_scope_does_not_widen_manager_permissions(app, client):
    """``admin:full`` on a manager's token never exceeds the manager's role."""
    mgr = _manager(app, perms=("dashboard.view",))
    h = _token(app, admin_id=mgr.id, scopes=["admin:full", "*"])
    r = client.post("/api/v1/tokens", json={"name": "x"}, headers=h)
    assert r.status_code == 403


def test_unbound_read_token_cannot_write(app, client):
    _owner(app)
    h = _token(app, admin_id=0, scopes=["read"])
    r = client.post("/api/v1/tokens", json={"name": "from-unbound"}, headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())


def test_unbound_token_without_explicit_full_flag_is_refused(app, client):
    _owner(app)
    h = _token(app, admin_id=0, scopes=[])
    r = client.get("/api/v1/tokens", headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())


def test_unbound_token_flagged_full_keeps_behaviour(app, client, monkeypatch):
    _owner(app)
    h = _token(app, admin_id=0, scopes=["admin:full"])
    assert client.get("/api/v1/tokens", headers=h).status_code == 200
    # B-07 (tests/test_sec_b07_unbound_tokens.py): minting by an unbound token
    # is refused by default and restored only by the owner's opt-in setting.
    r = client.post("/api/v1/tokens", json={"name": "integration"}, headers=h)
    assert r.status_code == 403, r.get_json()
    monkeypatch.setenv("HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT", "1")
    r = client.post("/api/v1/tokens", json={"name": "integration"}, headers=h)
    assert r.status_code in (200, 201), r.get_json()


def test_env_token_unchanged(app, client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "env-integration-token-123")
    _owner(app)
    h = {"Authorization": "Bearer env-integration-token-123"}
    assert client.get("/api/v1/tokens", headers=h).status_code == 200
