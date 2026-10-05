"""B-04 (docs/security/BYPASS_PATHS.md): an anonymous ``X-Tenant`` header must
not let a caller skip the per-tenant store key.

Before the fix the store-key guard verified the key against ``g.tenant_id``
(the header's tenant) while every store handler works on tenant 1 (public
endpoints) or on the tenant inside the signed store token. Naming a tenant
that has no key (or whose key the caller knows) therefore opened tenant 1's
store without tenant 1's key.

Rule after the fix: the key is verified against the tenant the handler will
actually use — the store token's tenant when a valid store token is presented,
otherwise tenant 1 — and ``X-Tenant`` never changes that.
"""
from __future__ import annotations

from sec_tenant_helpers import app, client, seen_tid, tenants  # noqa: F401


def _key(app, tid):
    from app.radius.services.store_key import get_or_create_store_key
    with app.app_context():
        return get_or_create_store_key(tid)


def test_sanity_tenant1_key_enforced_without_header(app, client):
    tenants(app)
    k1 = _key(app, 1)
    assert client.get("/api/v1/store/ping").status_code == 403
    assert client.get("/api/v1/store/ping",
                      headers={"X-Store-Key": k1}).status_code == 200


def test_x_tenant_of_keyless_tenant_does_not_skip_tenant1_key(app, client):
    _a, b = tenants(app)
    _key(app, 1)                                   # tenant B has NO key
    r = client.get("/api/v1/store/ping", headers={"X-Tenant": b.slug})
    assert r.status_code == 403, r.get_json()
    r = client.post("/api/v1/store/register", headers={"X-Tenant": b.slug},
                    json={"display_name": "Eve Attacker X", "mobile": "0599000001",
                          "password": "secret-99"})
    assert r.status_code == 403, r.get_json()
    r = client.post("/api/v1/store/login", headers={"X-Tenant": b.slug},
                    json={"mobile": "0599000001", "password": "secret-99"})
    assert r.status_code == 403, r.get_json()


def test_cross_tenant_key_of_b_does_not_open_tenant1_store(app, client):
    """Both tenants have keys; B's key + ``X-Tenant: B`` must not open the
    store whose handlers run on tenant 1."""
    _a, b = tenants(app)
    k1 = _key(app, 1)
    kb = _key(app, b.id)
    assert k1 != kb
    r = client.get("/api/v1/store/ping",
                   headers={"X-Tenant": b.slug, "X-Store-Key": kb})
    assert r.status_code == 403, r.get_json()
    # the legitimate key still works, header or not
    r = client.get("/api/v1/store/ping",
                   headers={"X-Tenant": b.slug, "X-Store-Key": k1})
    assert r.status_code == 200, r.get_json()


def test_store_token_calls_also_verify_tenant1_key(app, client):
    """An authenticated store call (token for tenant 1) + ``X-Tenant`` of a
    keyless tenant is refused without tenant 1's key, accepted with it."""
    _a, b = tenants(app)
    k1 = _key(app, 1)
    from app.radius.services.store_token import issue_store_token
    with app.app_context():
        tok = issue_store_token(card_user_id=999999, tenant_id=1)
    h = {"Authorization": f"Bearer {tok}", "X-Tenant": b.slug}
    r = client.get("/api/v1/store/me", headers=h)
    assert r.status_code == 403, r.get_json()
    r = client.get("/api/v1/store/me", headers={**h, "X-Store-Key": k1})
    assert r.status_code != 403, r.get_json()


def test_no_key_anywhere_keeps_legacy_open_store(app, client):
    """Compatibility: before the first store publish no key exists → open."""
    _a, b = tenants(app)
    assert client.get("/api/v1/store/ping").status_code == 200
    assert client.get("/api/v1/store/ping",
                      headers={"X-Tenant": b.slug}).status_code == 200


def test_store_admin_endpoints_untouched(app, client):
    """/store/admin/* stays on the admin API token, not the store key."""
    _a, b = tenants(app)
    _key(app, 1)
    r = client.get("/api/v1/store/admin/deposits", headers={"X-Tenant": b.slug})
    assert r.status_code != 403 or (r.get_json() or {}).get(
        "error", {}).get("code") != "store_key_invalid"


def test_guard_independent_of_hook_order(app, client):
    """Latent form of B-04. Today the store-key guard happens to be registered
    BEFORE the tenant resolver (app/__init__.py), so ``g.tenant_id`` is still
    unset when it runs and it falls back to tenant 1. Any reordering (or any
    earlier hook that resolves the tenant) re-opens the bypass. Simulate
    "resolver first" and require the guard to stay closed."""
    _a, b = tenants(app)
    _key(app, 1)

    def _resolve_tenant_first():
        from flask import g, request
        from app.radius.db.repos import tenants_repo
        slug = (request.headers.get("X-Tenant") or "").strip()
        t = tenants_repo.get_by_slug(slug) if slug else None
        if t:
            g.tenant_id = int(t.id)

    app.before_request_funcs.setdefault(None, []).insert(0, _resolve_tenant_first)
    r = client.get("/api/v1/store/ping", headers={"X-Tenant": b.slug})
    assert r.status_code == 403, r.get_json()
