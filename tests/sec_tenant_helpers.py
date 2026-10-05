"""Shared fixtures/helpers for the tenant-isolation security tests
(``tests/test_sec_b*.py``, docs/security/SEC_FIXES_APP.md).

Not a test module itself (no ``test_`` prefix). Every test gets its own
temporary SQLite file — never a real database, no network, and every token is
minted inside that temporary database.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from uuid import uuid4

import pytest

PW = "pw-123456"


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_secfix_")
    db_file = os.path.join(tmp, "t.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in ("HOBERADIUS_ENV", "FLASK_ENV", "HOBERADIUS_API_TOKENS",
              "HOBERADIUS_API_AUTH_REQUIRED", "HOBERADIUS_DIAG_ENABLED",
              "HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT"):
        monkeypatch.delenv(k, raising=False)
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


def seen_tid(app, path):
    return app.config["_SEEN_TENANT"].get(path)


def owner(app):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        pid = admins_repo.primary_admin_id()
        if pid is None:
            admins_repo.create_admin(username="owner_x", password="owner-pass",
                                     full_name="Owner", is_super_admin=True)
            pid = admins_repo.primary_admin_id()
        return admins_repo.get_admin(pid)


def tenants(app, *extra_slugs):
    """Tenant A (default, id 1), tenant B (slug ``tenant-b``) and any extra."""
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo
    out = []
    with app.app_context():
        tenants_repo.ensure_default_tenant()
        out.append(tenants_repo.get_tenant(1))
        for slug in ("tenant-b", *extra_slugs):
            out.append(tenants_repo.get_by_slug(slug) or tenants_repo.create_tenant(
                Tenant(id=None, slug=slug, name=slug.title())))
    return out


def manager(app, perms=("dashboard.view",), *, tenant_id=1, is_super_admin=False,
            username=None, extra_tenants=()):
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    owner(app)
    with app.app_context():
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}",
                                       display_name="r", permissions=tuple(perms))
        u = username or f"m_{uuid4().hex[:8]}"
        adm = admins_repo.create_admin(username=u, password=PW,
                                       full_name="M", is_super_admin=is_super_admin,
                                       role_id=role.id)
        for tid in (tenant_id, *extra_tenants):
            tenants_repo.add_membership(TenantMembership(
                id=None, tenant_id=tid, admin_id=adm.id, role_id=role.id))
    return adm


def token(app, *, admin_id, scopes, tenant_id=1):
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=tenant_id, name=f"t{uuid4().hex[:6]}", scopes=list(scopes),
            created_by=int(admin_id))
    return {"Authorization": f"Bearer {plain}"}


def login_web(app, client, admin, tenant_id):
    from app.radius.auth.session_helpers import set_current_admin
    with app.test_request_context("/"):
        from flask import session
        set_current_admin(admin, tenant_id)
        data = dict(session)
    with client.session_transaction() as s:
        s.update(data)


def basic(username, password=PW):
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}
