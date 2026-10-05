"""SEC F-1 — the MikroTik dashboard / operations pages must never ship an env
API token (``HOBERADIUS_API_TOKENS``, unbound, owner-level) to the browser.

Before the fix ``mt_dashboard._ui_api_token()`` rendered the FIRST env token
into ``data-mt-api-token`` on /admin/radius/mt/<id>/dashboard and
/admin/radius/mt/operations, so any panel user able to open those pages could
copy a credential that bypasses every permission on every tenant.

After the fix the page receives a short-lived DB token minted for the
logged-in admin (``created_by`` = that admin, ``tenant_id`` = the session
tenant, ``expires_at`` set, ``login:`` prefix so password change / disable
revokes it). The API then enforces the admin's own permissions and tenant.
All token values below are TEST values.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import tempfile

import pytest

ENV_TOKENS = "f1-envtok-AAAAAAAAAAAAAAAA0001,f1-envtok-BBBBBBBBBBBBBBBB0002"
DEV_TOKEN = "dev-token-please-change"


@pytest.fixture()
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sec_f1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("FLASK_SECRET", "f1-test-secret")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", ENV_TOKENS)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        a = admins_repo.create_admin(username="op", password="op123456", full_name="op")
        created.config["_admin_id"] = int(a.id)
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _now():
    return _dt.datetime.utcnow().isoformat() + "Z"


def _ensure_tenant(tid):
    from app.radius.db.connection import db
    if not db().execute("SELECT 1 FROM tenants WHERE id=?", (tid,)).fetchone():
        db().execute(
            "INSERT INTO tenants(id, slug, name, display_name, created_at) VALUES(?,?,?,?,?)",
            (tid, f"t{tid}", f"T{tid}", f"T{tid}", _now()))
        db().commit()


def _nas(tid=1, name="RB"):
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, enabled, "
        "connection_mode, coa_port, created_at, updated_at) "
        "VALUES(?,?,?,?,'mikrotik',1,'direct',3799,?,?)",
        (tid, name, f"198.51.100.{10 + tid}", "s3cr3t", _now(), _now()))
    db().commit()
    return int(cur.lastrowid)


def _client(app, *, admin_id, tenant_id=1, super_=True, user="op"):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=admin_id, admin_user=user, is_super_admin=super_,
                 tenant_id=tenant_id, _csrf_token="t")
    return c


def _page_token(html: str) -> str:
    m = re.search(r'data-mt-api-token="([^"]*)"', html)
    assert m, "data-mt-api-token attribute missing"
    return m.group(1)


def _assert_no_env_token(html: str):
    for tok in ENV_TOKENS.split(","):
        assert tok not in html
    assert DEV_TOKEN not in html


# ───────────────────────── the leak (fails before the fix) ─────────────────────────
def test_dashboard_html_contains_no_env_token(app):
    with app.app_context():
        nid = _nas()
    res = _client(app, admin_id=app.config["_admin_id"]).get(f"/admin/radius/mt/{nid}/dashboard")
    assert res.status_code == 200
    _assert_no_env_token(res.get_data(as_text=True))


def test_operations_html_contains_no_env_token(app):
    with app.app_context():
        _nas()
    res = _client(app, admin_id=app.config["_admin_id"]).get("/admin/radius/mt/operations")
    assert res.status_code == 200
    _assert_no_env_token(res.get_data(as_text=True))


def test_dev_fallback_token_never_rendered(app, monkeypatch):
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    with app.app_context():
        nid = _nas()
    html = _client(app, admin_id=app.config["_admin_id"]).get(
        f"/admin/radius/mt/{nid}/dashboard").get_data(as_text=True)
    assert DEV_TOKEN not in html


# ───────────────────────── the replacement token ─────────────────────────
def test_page_token_is_short_lived_and_bound_to_session_admin(app):
    with app.app_context():
        nid = _nas()
    html = _client(app, admin_id=app.config["_admin_id"]).get(
        f"/admin/radius/mt/{nid}/dashboard").get_data(as_text=True)
    tok = _page_token(html)
    assert tok
    with app.app_context():
        from app.radius.db.repos import api_tokens_repo
        rec = api_tokens_repo.resolve_by_plain(tok)
        assert rec is not None
        assert int(rec["created_by"]) == app.config["_admin_id"]
        assert int(rec["tenant_id"]) == 1
        assert rec["name"].startswith(api_tokens_repo.LOGIN_TOKEN_PREFIX)
        exp = _dt.datetime.fromisoformat(str(rec["expires_at"]).replace("Z", ""))
        assert _dt.datetime.utcnow() < exp <= _dt.datetime.utcnow() + _dt.timedelta(hours=8, minutes=1)


def test_page_token_reused_within_session(app):
    """No new api_tokens row per page view."""
    with app.app_context():
        nid = _nas()
    c = _client(app, admin_id=app.config["_admin_id"])
    t1 = _page_token(c.get(f"/admin/radius/mt/{nid}/dashboard").get_data(as_text=True))
    t2 = _page_token(c.get(f"/admin/radius/mt/{nid}/dashboard").get_data(as_text=True))
    t3 = _page_token(c.get("/admin/radius/mt/operations").get_data(as_text=True))
    assert t1 == t2 == t3


def test_page_token_authenticates_the_api_as_that_admin(app):
    with app.app_context():
        nid = _nas()
    tok = _page_token(_client(app, admin_id=app.config["_admin_id"]).get(
        f"/admin/radius/mt/{nid}/dashboard").get_data(as_text=True))
    api = app.test_client()
    res = api.get("/api/admin/me", headers={"Authorization": f"Bearer {tok}"})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["data"]["admin"]["id"] == app.config["_admin_id"]


def test_page_token_dies_with_disabled_admin(app):
    with app.app_context():
        nid = _nas()
    tok = _page_token(_client(app, admin_id=app.config["_admin_id"]).get(
        f"/admin/radius/mt/{nid}/dashboard").get_data(as_text=True))
    with app.app_context():
        from app.radius.db.repos import api_tokens_repo
        api_tokens_repo.revoke_admin_tokens(app.config["_admin_id"])   # password change path
    res = app.test_client().get("/api/admin/me", headers={"Authorization": f"Bearer {tok}"})
    assert res.status_code == 401


def test_no_session_admin_gets_no_token(app):
    from app.radius.routes import mt_dashboard
    with app.test_request_context("/admin/radius/mt/operations"):
        from flask import session
        session.clear()
        assert mt_dashboard._ui_api_token() == ""


# ───────────────────────── cross-tenant ─────────────────────────
def test_tenant2_admin_token_is_bound_to_tenant2_and_cannot_jump(app):
    with app.app_context():
        from app.radius.core.constants import ALL_PERMISSIONS
        from app.radius.core.tenant import TenantMembership
        from app.radius.db.repos import admins_repo, tenants_repo
        _ensure_tenant(2)
        role = admins_repo.create_role(name="t2full", display_name="T2",
                                       permissions=tuple(ALL_PERMISSIONS))
        b = admins_repo.create_admin(username="t2op", password="op123456",
                                     full_name="t2", role_id=role.id)
        tenants_repo.add_membership(TenantMembership(id=None, tenant_id=2, admin_id=int(b.id)))
        nid2 = _nas(tid=2, name="T2-RB")
        _nas(tid=1, name="T1-SECRET-RB")
        bid = int(b.id)
    # mint exactly as the page does, for a tenant-2 session
    from flask import g, session
    from app.radius.routes import mt_dashboard
    with app.test_request_context(f"/admin/radius/mt/{nid2}/dashboard"):
        session.update(admin_id=bid, tenant_id=2)
        g.tenant_id = 2
        tok = mt_dashboard._ui_api_token()
    assert tok and tok not in ENV_TOKENS.split(",")
    with app.app_context():
        from app.radius.db.repos import api_tokens_repo
        rec = api_tokens_repo.resolve_by_plain(tok)
        assert int(rec["tenant_id"]) == 2 and int(rec["created_by"]) == bid
    me = app.test_client().get("/api/admin/me", headers={
        "Authorization": f"Bearer {tok}", "X-Tenant-Id": "1"})
    assert me.status_code == 200
    assert int(me.get_json()["data"]["tenant_id"]) == 2      # header cannot jump
    nas = app.test_client().get("/api/v1/nas", headers={
        "Authorization": f"Bearer {tok}", "X-Tenant-Id": "1"})
    assert "T1-SECRET-RB" not in nas.get_data(as_text=True)
