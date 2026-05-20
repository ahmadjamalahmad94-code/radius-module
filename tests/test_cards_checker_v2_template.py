"""R13.A.2 regression: Cards Checker v2 template + route.

Side-by-side with v1: /admin/radius/cards/checker/v2 renders the new
operations-room layout, server-side, with the same `result` payload v1
uses. A.3 will layer AJAX search on top of this same markup. A.4 will
flip the default route to use this template.

Coverage:
 1. /v2 with no query renders the empty hero state.
 2. /v2 with too-long query renders the error state.
 3. /v2 with a non-existent card renders the not-found state.
 4. /v2 with a real card renders the operations-room with
    data-cc-section markers we rely on for selectors in A.3 (JS) and
    for permission gating in C.
 5. The v2 page links the v2 CSS bundle, not v1 inline styles.
 6. POST is intentionally NOT handled on /v2 — submits go to /checker.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r13a2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def _seed_card(conn, *, username):
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
        VALUES (1, 'B-V2-1', ?, 0, 0, 0, 'seed', 'active', ?, '{}')
    """, (plan_id, now))
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("""
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id,
             used, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 0, 0, ?)
    """, (batch_id, username, plan_id, now))
    return username


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1


# ─────────── empty / error / not-found states ───────────

def test_v2_empty_state_when_no_query(app):
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "cc-state-empty" in body
    assert "مركز فحص البطاقات جاهز" in body


def test_v2_too_long_query_shows_error(app):
    client = app.test_client()
    _login(client)
    long_q = "x" * 129
    resp = client.get(f"/admin/radius/cards/checker/v2?query={long_q}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "cc-state-error" in body


def test_v2_not_found_state(app):
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2?query=no-such-card")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "لم نَجد بطاقة" in body
    assert "no-such-card" in body


# ─────────── found state ───────────

def test_v2_found_renders_operations_room(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_card(c, username="card-v2-7777")

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2?query=card-v2-7777")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # username visible
    assert "card-v2-7777" in body
    # major sections present — A.3 (JS) and C (ACL) hook off these markers
    assert 'data-cc-section="identity"' in body
    assert 'data-cc-section="stats"' in body
    assert 'data-cc-section="operations"' in body
    # operations buttons present (some disabled, but should render)
    assert 'data-cc-op="disconnect"' in body
    assert 'data-cc-op="lock-mac"' in body
    assert 'data-cc-op="reset-usage"' in body
    assert 'data-cc-op="disable-enable"' in body
    assert 'data-cc-op="delete"' in body
    # ACL hook points present for fields (used in C)
    assert 'data-cc-field="username"' in body
    assert 'data-cc-field="mac_address"' in body
    assert 'data-cc-field="ip_address"' in body


# ─────────── presentation ───────────

def test_v2_uses_external_css_not_inline(app):
    """v2 must link the dedicated stylesheet rather than embedding styles
    — this matters for caching, CSP, and maintainability."""
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2")
    body = resp.get_data(as_text=True)
    assert "cards_checker_v2.css" in body, \
        "v2 page must link the dedicated CSS bundle"


def test_v2_operation_forms_submit_to_v1_route(app):
    """The v2 page renders new forms but they POST back to /cards/checker
    — the proven legacy POST handler stays the source of truth until A.4."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_card(c, username="card-ops-routing")

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2?query=card-ops-routing")
    body = resp.get_data(as_text=True)
    # forms target /admin/radius/cards/checker (not /v2)
    assert 'action="/admin/radius/cards/checker"' in body


def test_v2_does_not_accept_post(app):
    """v2 is GET-only — operations route through the v1 POST handler.
    A direct POST to /v2 must NOT successfully process an op; it lands on
    Flask's 405 OR is bounced by CSRF middleware to login/error (302) —
    either way, the v2 view function never sees the POST.
    """
    client = app.test_client()
    _login(client)
    resp = client.post("/admin/radius/cards/checker/v2",
                       data={"op": "disconnect", "username": "x"})
    # whatever Flask + middleware decide, the only forbidden outcome is
    # "200 — op was processed by /v2".
    assert resp.status_code != 200


# ─────────── legacy unaffected ───────────

def test_v1_still_works(app):
    """A.2 is a side-by-side preview, not a swap. v1 must keep rendering
    until A.4 explicitly retires it."""
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker?query=anything")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # v1 page does NOT link the v2 stylesheet
    assert "cards_checker_v2.css" not in body
