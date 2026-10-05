"""B-07 (docs/security/BYPASS_PATHS.md): unbound DB tokens (``created_by=0``).

After b38258a4 an unbound token needs an explicit scope (``[]`` → 403). What
remained open:

1. an unbound ``admin:full`` token could mint MORE unbound tokens with any
   scope (``*`` included) — a leaked token perpetuates itself and survives its
   own revocation;
2. an unbound token is "owner-level", so a token bound to tenant B reached the
   server-wide owner endpoints: every tenant's row (``/api/v1/tenants``,
   including PATCH of tenant A) and the whole-database backups.

Fix (safe defaults; each restorable by an env setting — OWNER DECISION):
  * ``HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT=1``        → (1) old behaviour
  * ``HOBERADIUS_ALLOW_UNBOUND_TOKEN_SERVER_WIDE=1`` → (2) old behaviour
Env tokens (``HOBERADIUS_API_TOKENS``, set by the operator on the server) and
tokens bound to an owner are unchanged.
"""
from __future__ import annotations

import pytest

from sec_tenant_helpers import (  # noqa: F401
    app, client, manager, owner, tenants, token,
)


def _sub(app, tid, username, full_name):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    with app.app_context():
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username=username, password="pw-x", tenant_id=tid,
            status="enabled", full_name=full_name))


# ───────────────────── 1. minting (reproducers) ─────────────────────

def test_unbound_full_token_cannot_mint_star_token(app, client):
    owner(app)
    h = token(app, admin_id=0, scopes=["admin:full"])
    r = client.post("/api/v1/tokens", json={"name": "child", "scopes": ["*"]}, headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())


def test_unbound_full_token_cannot_mint_default_scoped_token(app, client):
    owner(app)
    h = token(app, admin_id=0, scopes=["*"])
    r = client.post("/api/v1/tokens", json={"name": "child"}, headers=h)
    assert r.status_code == 403, (r.status_code, r.get_json())
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        assert not [t for t in api_tokens_repo.list_tokens(1) if t["name"] == "child"]


def test_unbound_mint_flag_restores_old_behaviour(app, client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT", "1")
    owner(app)
    h = token(app, admin_id=0, scopes=["admin:full"])
    r = client.post("/api/v1/tokens", json={"name": "child", "scopes": ["*"]}, headers=h)
    assert r.status_code in (200, 201), r.get_json()


def test_unbound_token_may_still_list_and_revoke(app, client):
    owner(app)
    h = token(app, admin_id=0, scopes=["admin:full"])
    assert client.get("/api/v1/tokens", headers=h).status_code == 200


def test_bound_owner_and_env_tokens_still_mint(app, client, monkeypatch):
    own = owner(app)
    h = token(app, admin_id=own.id, scopes=["admin:full"])
    r = client.post("/api/v1/tokens", json={"name": "by-owner"}, headers=h)
    assert r.status_code in (200, 201), r.get_json()
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "env-integration-token-123")
    r = client.post("/api/v1/tokens", json={"name": "by-env"},
                    headers={"Authorization": "Bearer env-integration-token-123"})
    assert r.status_code in (200, 201), r.get_json()


# ─────────────── 2. server-wide reach (cross-tenant) ───────────────

def test_unbound_token_of_b_cannot_list_or_patch_other_tenants(app, client):
    a, b = tenants(app)
    owner(app)
    hb = token(app, admin_id=0, scopes=["admin:full"], tenant_id=b.id)
    r = client.get("/api/v1/tenants", headers=hb)
    assert r.status_code == 403, (r.status_code, r.get_json())
    r = client.get(f"/api/v1/tenants/{a.id}", headers=hb)
    assert r.status_code == 403, (r.status_code, r.get_json())
    r = client.patch(f"/api/v1/tenants/{a.id}", json={"name": "pwned"}, headers=hb)
    assert r.status_code == 403, (r.status_code, r.get_json())
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        assert tenants_repo.get_tenant(a.id).name != "pwned"


def test_unbound_token_cannot_reach_whole_db_backups(app, client):
    _a, b = tenants(app)
    owner(app)
    hb = token(app, admin_id=0, scopes=["admin:full"], tenant_id=b.id)
    r = client.get("/api/v1/backups/status", headers=hb)
    assert r.status_code == 403, (r.status_code, r.get_json())


def test_server_wide_flag_restores_old_behaviour(app, client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ALLOW_UNBOUND_TOKEN_SERVER_WIDE", "1")
    _a, b = tenants(app)
    owner(app)
    hb = token(app, admin_id=0, scopes=["admin:full"], tenant_id=b.id)
    assert client.get("/api/v1/tenants", headers=hb).status_code == 200


def test_owner_and_env_tokens_keep_server_wide_access(app, client, monkeypatch):
    tenants(app)
    own = owner(app)
    h = token(app, admin_id=own.id, scopes=["admin:full"])
    assert client.get("/api/v1/tenants", headers=h).status_code == 200
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "env-integration-token-123")
    assert client.get("/api/v1/tenants", headers={
        "Authorization": "Bearer env-integration-token-123"}).status_code == 200


# ─────────────── cross-tenant data with unbound tokens ───────────────

@pytest.mark.parametrize("spoof", [{}, {"X-Tenant": "tenant-b"}, {"X-Tenant-Id": "__B__"}])
def test_unbound_tokens_same_username_each_sees_own_tenant(app, client, spoof):
    _a, b = tenants(app)
    owner(app)
    _sub(app, 1, "ahmad", "ALPHA-A")
    _sub(app, b.id, "ahmad", "BRAVO-B")
    ha = token(app, admin_id=0, scopes=["admin:full"], tenant_id=1)
    hb = token(app, admin_id=0, scopes=["admin:full"], tenant_id=b.id)
    hdr = {k: (str(b.id) if v == "__B__" else v) for k, v in spoof.items()}
    ra = client.get("/api/v1/accounts/ahmad", headers={**ha, **hdr})
    rb = client.get("/api/v1/accounts/ahmad", headers={**hb, **hdr})
    assert ra.status_code == 200 and rb.status_code == 200, (ra.get_json(), rb.get_json())
    assert ra.get_json()["data"]["full_name"] == "ALPHA-A"
    assert rb.get_json()["data"]["full_name"] == "BRAVO-B"


def test_unbound_token_without_scope_still_refused(app, client):
    owner(app)
    for scopes in ([], ["cards.view"], ["bogus"]):
        h = token(app, admin_id=0, scopes=scopes)
        assert client.get("/api/v1/tokens", headers=h).status_code == 403, scopes
