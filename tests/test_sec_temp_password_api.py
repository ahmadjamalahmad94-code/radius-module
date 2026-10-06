"""SEC temp-password (owner decision 2026-10-06): a temporary / one-time
password MUST be changed before normal API use — enforced on the SERVER.

Before: ``POST /api/admin/login`` ignored ``admins.must_change_password`` and
minted a normal 7-day ``admin:full`` token, and HTTP Basic accepted the
one-time password on every endpoint — so the F-5 bootstrap password (and any
admin-reset temporary password) worked on the mobile app / API forever.

After (docs/security/SEC_TEMP_PASSWORD_API.md):
  * login for a flagged admin returns ``must_change_password: true`` and a
    SHORT-LIVED RESTRICTED credential (``token_type: password_change``);
  * that credential reaches only ``GET /api/admin/me``,
    ``POST /api/admin/password`` and ``POST /api/admin/logout``; anything else
    → 403 ``PASSWORD_CHANGE_REQUIRED``;
  * HTTP Basic for a flagged admin → 403 ``PASSWORD_CHANGE_REQUIRED``;
  * any other DB token of a flagged admin is held to the same restriction;
  * a successful change revokes the temporary credential and the admin's other
    login tokens, clears the flag, and normal login works again.
All values are TEST values in a temporary SQLite database.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sec_tenant_helpers import (  # noqa: F401
    PW, app, basic, client, manager, owner, tenants, token,
)

NEW_PW = "brand-new-pass-9241"
CODE = "PASSWORD_CHANGE_REQUIRED"
PERMS = ("dashboard.view", "subscribers.view", "nas.view", "admins.view",
         "cards.view", "roles.view")


def _flag(app, admin_id, on=True):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        admins_repo.update_admin(int(admin_id), must_change_password=1 if on else 0)


def _login(client, username, password=PW, **kw):
    r = client.post("/api/admin/login",
                    json={"username": username, "password": password}, **kw)
    return r, (r.get_json() or {}).get("data") or {}


def _bearer(tok, **extra):
    return {"Authorization": f"Bearer {tok}", **extra}


def _code(r):
    return (((r.get_json() or {}).get("error")) or {}).get("code")


# a sample of protected endpoints — tenant-scoped lists, admin/RBAC, a write
# and a token-minting endpoint.
_SAMPLE = [
    ("GET", "/api/v1/accounts"),
    ("GET", "/api/v1/nas"),
    ("GET", "/api/v1/admins"),
    ("GET", "/api/v1/roles"),
    ("GET", "/api/v1/tokens"),
    ("POST", "/api/v1/tokens"),
    ("POST", "/api/v1/accounts"),
]


@pytest.fixture
def flagged(app):
    tenants(app)
    m = manager(app, PERMS)
    _flag(app, m.id)
    return m


# ── login ─────────────────────────────────────────────────────────────────

def test_login_returns_restricted_credential_and_flag(app, client, flagged):
    r, d = _login(client, flagged.username)
    assert r.status_code == 200
    assert d.get("must_change_password") is True
    assert d.get("token_type") == "password_change"
    assert d.get("restricted") is True
    assert (d.get("admin") or {}).get("must_change_password") is True
    assert d.get("permissions") == []
    assert set(d.get("allowed_endpoints") or []) == {
        "GET /api/admin/me", "POST /api/admin/password", "POST /api/admin/logout"}
    # short-lived: well under the 7-day normal session
    exp = datetime.fromisoformat(str(d["expires_at"]).replace("Z", ""))
    assert exp - datetime.utcnow() <= timedelta(minutes=16)
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        rec = api_tokens_repo.resolve_by_plain(d["token"])
    assert rec["scopes"] == ["password_change"]
    assert int(rec["created_by"]) == int(flagged.id)


def test_restricted_credential_refused_on_other_endpoints(app, client, flagged):
    _r, d = _login(client, flagged.username)
    h = _bearer(d["token"])
    for method, path in _SAMPLE:
        r = client.open(path, method=method, headers=h, json={})
        assert r.status_code == 403, (method, path, r.status_code)
        assert _code(r) == CODE, (method, path, r.get_json())


def test_restricted_credential_allowed_endpoints(app, client, flagged):
    _r, d = _login(client, flagged.username)
    me = client.get("/api/admin/me", headers=_bearer(d["token"]))
    assert me.status_code == 200
    body = me.get_json()["data"]
    assert body["must_change_password"] is True
    assert body["admin"]["username"] == flagged.username
    assert body["permissions"] == []
    out = client.post("/api/admin/logout", headers=_bearer(d["token"]))
    assert out.status_code == 200
    again = client.get("/api/admin/me", headers=_bearer(d["token"]))
    assert again.status_code == 401


def test_x_tenant_id_cannot_widen_restricted_credential(app, client, flagged):
    _r, d = _login(client, flagged.username)
    for tid in ("1", "2"):
        h = _bearer(d["token"], **{"X-Tenant-Id": tid, "X-Tenant": "tenant-b"})
        r = client.get("/api/v1/accounts", headers=h)
        assert r.status_code == 403 and _code(r) == CODE
        me = client.get("/api/admin/me", headers=h)
        assert me.status_code == 200
        assert int(me.get_json()["data"]["tenant_id"]) == int(d["tenant_id"])


def test_cross_tenant_temp_credential_cannot_reach_tenant_b(app, client):
    tenants(app)
    a = manager(app, PERMS, tenant_id=1)
    _flag(app, a.id)
    _r, d = _login(client, a.username)
    assert int(d["tenant_id"]) == 1
    h = _bearer(d["token"], **{"X-Tenant-Id": "2"})
    for path in ("/api/v1/accounts", "/api/v1/nas", "/api/v1/admins"):
        r = client.get(path, headers=h)
        assert r.status_code == 403 and _code(r) == CODE, path


# ── HTTP Basic ────────────────────────────────────────────────────────────

def test_basic_refused_for_flagged_admin(app, client, flagged):
    r = client.get("/api/v1/accounts", headers=basic(flagged.username))
    assert r.status_code == 403 and _code(r) == CODE
    r = client.get("/api/admin/me", headers=basic(flagged.username))
    assert r.status_code in (401, 403)
    assert r.status_code != 200
    # a wrong password stays a plain 401 (no flag oracle)
    r = client.get("/api/v1/accounts", headers=basic(flagged.username, "wrong-pw-x"))
    assert r.status_code == 401 and _code(r) != CODE


def test_basic_refused_with_tenant_header(app, client, flagged):
    h = {**basic(flagged.username), "X-Tenant-Id": "2"}
    r = client.get("/api/v1/nas", headers=h)
    assert r.status_code == 403 and _code(r) == CODE


# ── pre-existing tokens of a flagged admin ───────────────────────────────

def test_existing_full_token_of_flagged_admin_is_restricted(app, client):
    tenants(app)
    m = manager(app, PERMS)
    h = token(app, admin_id=m.id, scopes=["admin:full"])
    assert client.get("/api/v1/nas", headers=h).status_code == 200
    _flag(app, m.id)
    r = client.get("/api/v1/accounts", headers=h)
    assert r.status_code == 403 and _code(r) == CODE


# ── change password ──────────────────────────────────────────────────────

def _change(client, tok, current=PW, new=NEW_PW):
    return client.post("/api/admin/password", headers=_bearer(tok), json={
        "current_password": current, "new_password": new, "confirm_password": new})


def test_change_password_revokes_and_normal_login_works(app, client, flagged):
    other = token(app, admin_id=flagged.id, scopes=["admin:full"])
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        _rec, old_login = api_tokens_repo.create_token(
            tenant_id=1, name=f"{api_tokens_repo.LOGIN_TOKEN_PREFIX}{flagged.username}:old",
            scopes=["admin:full"], created_by=int(flagged.id))
    _r, d = _login(client, flagged.username)
    tmp = d["token"]
    r = _change(client, tmp)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["updated"] is True
    assert body["reauth_required"] is True
    # the temporary credential is revoked …
    assert client.get("/api/admin/me", headers=_bearer(tmp)).status_code == 401
    # … and so are the admin's other login tokens
    assert client.get("/api/admin/me", headers=_bearer(old_login)).status_code == 401
    # the flag is cleared
    from app.radius.db.repos import admins_repo
    with app.app_context():
        assert not admins_repo.get_admin(int(flagged.id)).must_change_password
    # the old (temporary) password no longer logs in
    r, _d = _login(client, flagged.username)
    assert r.status_code == 401
    # normal login with the new password → a normal full credential
    r, d2 = _login(client, flagged.username, NEW_PW)
    assert r.status_code == 200
    assert d2.get("must_change_password") is False
    assert d2.get("token_type") == "session"
    assert client.get("/api/v1/nas", headers=_bearer(d2["token"])).status_code == 200
    # a non-login integration token survives (flag gone → normal again)
    assert client.get("/api/v1/nas", headers=other).status_code == 200


def test_change_password_wrong_current_keeps_restriction(app, client, flagged):
    _r, d = _login(client, flagged.username)
    r = _change(client, d["token"], current="not-the-pw")
    assert r.status_code == 422
    r = client.get("/api/v1/accounts", headers=_bearer(d["token"]))
    assert r.status_code == 403 and _code(r) == CODE


def test_restricted_token_never_full_even_after_flag_cleared(app, client, flagged):
    """The credential itself is restricted (scope), not only the admin flag."""
    _r, d = _login(client, flagged.username)
    _flag(app, flagged.id, on=False)
    r = client.get("/api/v1/accounts", headers=_bearer(d["token"]))
    assert r.status_code == 403 and _code(r) == CODE


# ── unaffected credentials ───────────────────────────────────────────────

def test_unflagged_admin_login_unchanged(app, client):
    tenants(app)
    m = manager(app, PERMS)
    r, d = _login(client, m.username)
    assert r.status_code == 200
    assert d.get("must_change_password") is False
    assert d.get("token_type") == "session"
    assert (d.get("admin") or {}).get("must_change_password") is False
    assert d["permissions"]
    assert client.get("/api/v1/nas", headers=_bearer(d["token"])).status_code == 200
    assert client.get("/api/v1/nas", headers=basic(m.username)).status_code == 200
    # normal change keeps the calling session (legacy behaviour)
    r = _change(client, d["token"])
    assert r.status_code == 200
    assert r.get_json()["data"]["reauth_required"] is False
    assert client.get("/api/admin/me", headers=_bearer(d["token"])).status_code == 200


def test_env_token_unaffected(app, client, flagged, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "tp-env-token-AAAAAAAAAAAA0001")
    r = client.get("/api/v1/nas",
                   headers=_bearer("tp-env-token-AAAAAAAAAAAA0001"))
    assert r.status_code == 200


def test_f1_dashboard_token_unaffected_for_normal_admin(app, client):
    tenants(app)
    m = manager(app, PERMS)
    from flask import g, session

    from app.radius.routes import mt_dashboard
    with app.test_request_context("/admin/radius/mt/operations"):
        session.update(admin_id=int(m.id), tenant_id=1, admin_user=m.username)
        g.tenant_id = 1
        tok = mt_dashboard._ui_api_token()
    assert tok
    assert client.get("/api/v1/nas", headers=_bearer(tok)).status_code == 200
    # …and the same dashboard token is held back once the admin is flagged
    _flag(app, m.id)
    r = client.get("/api/v1/nas", headers=_bearer(tok))
    assert r.status_code == 403 and _code(r) == CODE
