"""R10.4 regression: /admin/radius/cards must paginate + support search.

Before R10.4 the route loaded `list_cards(limit=1000)` and dumped all
rows into the template — fine for 50 cards, slow and unscrollable at
2000+. We now:

  - Push pagination into the SQL (LIMIT + OFFSET) via `count_cards` +
    `list_cards(limit=, offset=)`.
  - Add `search` (LIKE on username) so admins can find a card by
    partial number without scrolling.
  - Whitelist `per_page` to {25, 50, 100} so a hostile query string
    can't force a 10k LIMIT.

Coverage:
 1. count_cards respects every filter combination.
 2. list_cards search matches partial username with LIKE.
 3. per_page is clamped — a bad value falls back to 50 (not 1000).
 4. page > pages_count is clamped to last page (no empty result for
    "page=999" when only 2 pages exist).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r104_")
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


def _seed_plan_and_batch(conn):
    """Cards has FK to access_plans + card_batches — seed parents once."""
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
        VALUES (1, 'B1', ?, 0, 0, 0, 'seed', 'active', ?, '{}')
    """, (plan_id, now))
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return plan_id, batch_id


def _seed_card(conn, *, username, plan_id, batch_id,
                used=0, revoked=0):
    conn.execute("""
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id,
             used, revoked, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (1, batch_id, username, "p", plan_id, used, revoked, _now()))


# ─────────── repo-layer (count_cards + search) ───────────

def test_count_cards_matches_list_with_same_filters(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos.cards_repo import count_cards, list_cards

        with transaction() as c:
            plan_id, batch_id = _seed_plan_and_batch(c)
            for i in range(7):
                _seed_card(c, username=f"u-{i:03d}",
                           plan_id=plan_id, batch_id=batch_id)
            # 2 of them used, 1 revoked
            c.execute("UPDATE cards SET used=1 WHERE username IN ('u-001','u-002')")
            c.execute("UPDATE cards SET revoked=1 WHERE username='u-003'")

        assert count_cards(1) == 7
        assert count_cards(1, used=True) == 2
        assert count_cards(1, used=False) == 5
        assert count_cards(1, revoked=True) == 1
        # And list_cards under the same filters returns the same row counts
        assert len(list_cards(1, used=True, limit=100)) == 2
        assert len(list_cards(1, revoked=True, limit=100)) == 1


def test_search_does_like_on_username(app):
    """Partial number search — typical admin pattern is to type a few
    digits and find one of many cards."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos.cards_repo import count_cards, list_cards

        with transaction() as c:
            plan_id, batch_id = _seed_plan_and_batch(c)
            _seed_card(c, username="20440", plan_id=plan_id, batch_id=batch_id)
            _seed_card(c, username="80391", plan_id=plan_id, batch_id=batch_id)
            _seed_card(c, username="80392", plan_id=plan_id, batch_id=batch_id)

        assert count_cards(1, search="803") == 2
        rows = list_cards(1, search="803", limit=100)
        assert {r.username for r in rows} == {"80391", "80392"}


# ─────────── route-layer (clamping + pagination metadata) ───────────

def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        # /cards (card list) is now RBAC-gated (cards.view); this synthetic
        # session represents the owner → mark it super so the guard allows it.
        s["is_super_admin"] = True


def test_route_clamps_per_page_to_whitelist(app):
    """A hostile or buggy `?per_page=99999` must not blow up the page."""
    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as c:
            plan_id, batch_id = _seed_plan_and_batch(c)
            for i in range(3):
                _seed_card(c, username=f"u-{i}",
                           plan_id=plan_id, batch_id=batch_id)

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards?per_page=99999")
    assert resp.status_code == 200
    # the rendered subtitle reports total + page, so we can sniff it for
    # an honest page-size hint (the dropdown will show 50 selected, the
    # default fallback when per_page isn't 25/50/100).
    body = resp.get_data(as_text=True)
    assert "50/صفحة" in body or 'value="50" selected' in body


def test_route_clamps_page_past_last(app):
    """page=999 with only 3 rows must return last page, not empty."""
    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as c:
            plan_id, batch_id = _seed_plan_and_batch(c)
            for i in range(3):
                _seed_card(c, username=f"u-{i}",
                           plan_id=plan_id, batch_id=batch_id)

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards?page=999&per_page=25")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # All 3 usernames must be there — the only page is page 1.
    for name in ("u-0", "u-1", "u-2"):
        assert name in body


def test_route_search_in_url_filters_results(app):
    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as c:
            plan_id, batch_id = _seed_plan_and_batch(c)
            _seed_card(c, username="20440", plan_id=plan_id, batch_id=batch_id)
            _seed_card(c, username="80391", plan_id=plan_id, batch_id=batch_id)

    client = app.test_client()
    _login(client)
    resp = client.get("/admin/radius/cards?q=2044")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "20440" in body
    assert "80391" not in body
