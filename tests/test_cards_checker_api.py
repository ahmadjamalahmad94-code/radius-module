"""R13.A.1 regression: Card Checker JSON API foundation.

The card-checker page is being rebuilt to be an AJAX-driven "operations
room" — see R13.A roadmap. A.1 lays the foundation: a JSON endpoint that
returns the same payload the existing HTML page renders, so the frontend
work in A.2+ can fetch() instead of full-reloading.

Contract:

  GET /admin/radius/cards/checker/api/lookup?q=<query>

    200 OK   { "ok": true,  "query": "...", "result": { exists, status, ... } }
    400 BAD  { "ok": false, "error": "...", "code": "empty_query|query_too_long" }

The 'not found' case is a 200 with `result.exists = false` — it's a
normal result of a search, not an error.

Coverage:
 1. Empty/missing q → 400 empty_query
 2. q over 128 chars → 400 query_too_long
 3. Valid q with no matching card → 200 + result.exists = false
 4. Valid q matching a real card → 200 + result.exists = true + fields
 5. Old HTML page still works (no breaking change)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r13a1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _seed_card_with_username(conn, *, username="card-001"):
    """Seed the FK chain (plan + batch) + the card row itself."""
    now = _now()
    conn.execute("""
        INSERT INTO access_plans (tenant_id, name, enabled, created_at)
        VALUES (1, 'p', 1, ?)
    """, (now,))
    plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("""
        INSERT INTO card_batches
            (tenant_id, batch_code, plan_id, count, generated, used,
             created_by, status, created_at, metadata)
        VALUES (1, 'B-API-1', ?, 0, 0, 0, 'seed', 'active', ?, '{}')
    """, (plan_id, now))
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("""
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id,
             used, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 0, 0, ?)
    """, (batch_id, username, plan_id, now))
    return username


def _login(client, app=None):
    """Log in as a REAL super-admin. A bare session injection is rejected by
    the RBAC route guard when NO_SEED leaves no admin row (pre-existing 403,
    same as the repaired test_online_list_separation), so we create one and
    authenticate through the login route."""
    from uuid import uuid4
    from app.radius.db.repos import admins_repo
    target = app or client.application
    with target.app_context():
        u = f"api_{uuid4().hex[:10]}"
        admins_repo.create_admin(
            username=u, password="api-pass", full_name="API Tester",
            is_super_admin=True,
        )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "api-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def test_empty_query_returns_400(app):
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/api/lookup")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body == {
        "ok": False,
        "error": "أدخل رقم بطاقة أو اسم دخول.",
        "code": "empty_query",
    }


def test_query_too_long_returns_400(app):
    client = app.test_client()
    _login(client)
    long_q = "x" * 129
    resp = client.get(f"/admin/radius/cards/checker/api/lookup?q={long_q}")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "query_too_long"


def test_not_found_returns_200_with_exists_false(app):
    """No matching card is NOT an error — the search ran successfully and
    the answer is 'doesn't exist'. The frontend renders that as an empty
    state, not an error toast."""
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/api/lookup?q=no-such-card")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["query"] == "no-such-card"
    assert body["result"]["exists"] is False
    assert body["result"]["status"] == "not_found"


def test_found_card_returns_full_payload(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_card_with_username(c, username="card-api-9001")

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/api/lookup?q=card-api-9001")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["query"] == "card-api-9001"

    r = body["result"]
    assert r["exists"] is True
    assert r["username"] == "card-api-9001"
    # Fields documented in card_checker.check_card() must be present so the
    # frontend doesn't have to defend against missing keys.
    for key in (
        "id", "status", "has_password", "used", "revoked",
        "created_at", "started_at", "expires_at", "remaining_seconds",
        "batch", "profile", "operations", "accounting_summary",
        "data_sources", "available_fields", "missing_fields",
    ):
        assert key in r, f"expected field {key!r} in result"

    # operations object describes what the frontend can wire actions for
    ops = r["operations"]
    for op in ("can_disconnect", "can_lock_mac", "can_reset_usage",
                "can_disable", "can_enable", "can_delete_permanently"):
        assert op in ops


def test_legacy_html_route_still_works(app):
    """The old full-reload page must keep functioning during the rebuild —
    we do not migrate users until A.4 swaps the template."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_card_with_username(c, username="legacy-card")

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker?query=legacy-card")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "legacy-card" in body


def test_q_and_query_parameter_names_both_accepted(app):
    """Frontend prefers `q`, legacy URLs use `query`. Accept both so a
    bookmarked URL doesn't break."""
    client = app.test_client()
    _login(client)
    resp_q = client.get("/admin/radius/cards/checker/api/lookup?q=foo")
    resp_query = client.get("/admin/radius/cards/checker/api/lookup?query=foo")
    assert resp_q.status_code == 200
    assert resp_query.status_code == 200
    assert resp_q.get_json()["query"] == "foo"
    assert resp_query.get_json()["query"] == "foo"
