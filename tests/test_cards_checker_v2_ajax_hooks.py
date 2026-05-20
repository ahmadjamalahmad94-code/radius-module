"""R13.A.3 regression: AJAX hooks on the v2 page.

The interactive layer in `cards_checker_v2.js` (R13.A.3) progressively
enhances the server-rendered v2 page. It needs three contracts from
the template:

  1. An element with id="cc-result" that wraps everything below the
     hero search bar — the script swaps its innerHTML on each search.
  2. A `<script>` reference to cards_checker_v2.js included on the
     page so the interactive layer actually loads.
  3. The search form has id="cc-search-form" and the input id="cc-search-input"
     — the script binds the submit handler via those ids.

If any of those three contracts breaks silently, the page will look
fine but searches will full-reload (degraded UX without an obvious
console error). These tests lock them in.

We also verify the legacy v1 page does NOT load the v2 script —
otherwise it would try to bind to elements that don't exist.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r13a3_")
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


def _seed_card(conn, *, username):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO access_plans (tenant_id, name, enabled, created_at)
        VALUES (1, 'p', 1, ?)
    """, (now,))
    plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("""
        INSERT INTO card_batches
            (tenant_id, batch_code, plan_id, count, generated, used,
             created_by, status, created_at, metadata)
        VALUES (1, 'B-A3', ?, 0, 0, 0, 'seed', 'active', ?, '{}')
    """, (plan_id, now))
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("""
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id,
             used, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 0, 0, ?)
    """, (batch_id, username, plan_id, now))


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1


def test_v2_includes_ajax_script(app):
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # script is loaded — the only feature switch for the whole interactive layer
    assert "cards_checker_v2.js" in body, \
        "v2 page must reference cards_checker_v2.js so AJAX works"


def test_v2_has_result_wrapper(app):
    """Empty state still has #cc-result wrapper — the JS uses it as the
    swap target regardless of the current sub-state (empty / not-found /
    found / error)."""
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2")
    body = resp.get_data(as_text=True)
    assert 'id="cc-result"' in body


def test_v2_search_form_has_id(app):
    """JS binds via #cc-search-form / #cc-search-input. Locked in."""
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2")
    body = resp.get_data(as_text=True)
    assert 'id="cc-search-form"' in body
    assert 'id="cc-search-input"' in body


def test_v2_found_card_still_inside_wrapper(app):
    """When a card is found, all the operations-room sections live inside
    #cc-result. The JS swaps them all in one shot."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_card(c, username="ajax-card-1")

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker/v2?query=ajax-card-1")
    body = resp.get_data(as_text=True)
    # #cc-result must wrap the rendered content
    start = body.find('id="cc-result"')
    assert start > -1
    # at least one section marker must come AFTER the wrapper open tag
    section_pos = body.find('data-cc-section=', start)
    assert section_pos > start


def test_legacy_url_loads_v2_script_after_swap(app):
    """R13.A.4: /cards/checker now renders the v2 template, which
    includes the v2 script. Before A.4 this test was inverted (asserted
    'NOT in body'); after A.4 it must be present."""
    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards/checker?query=anything")
    body = resp.get_data(as_text=True)
    assert "cards_checker_v2.js" in body, \
        "after A.4, legacy /cards/checker URL serves the v2 template"
