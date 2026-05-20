"""R9.0 regression: users list must:
  1. Exclude card-mirror rows (user_type='card') by default — every
     card generation creates a parallel subscribers row with
     user_type='card'. On a busy tenant this floods the page and hides
     real subscribers (the user reported a real subscriber 'ahmad' at
     id=37 being invisible behind 2020 card rows).
  2. Search via SQL (LIKE on username/full_name/mobile) BEFORE LIMIT,
     so the search box finds a name even when there are 1000+ records.
  3. Allow explicit user_type='card' or user_type=None for callers that
     genuinely want the full pool.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r90_")
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


def _seed_one(tenant_id, username, *, user_type="subscriber",
              full_name="", mobile=""):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="x",
        user_type=user_type, full_name=full_name, mobile=mobile,
        status="enabled",
    ))


def test_default_excludes_cards(app):
    with app.app_context():
        # 3 real subscribers + 5 card-mirrors
        _seed_one(1, "ahmad",  user_type="subscriber", full_name="Ahmad")
        _seed_one(1, "sara",   user_type="subscriber")
        _seed_one(1, "omar",   user_type="subscriber")
        for n in ("11111111", "22222222", "33333333", "44444444", "55555555"):
            _seed_one(1, n, user_type="card")

        from app.radius.services.users import get_users_service
        items = list(get_users_service().list(limit=100))

        names = sorted(u.username for u in items)
        assert names == ["ahmad", "omar", "sara"], \
            f"cards leaked into users list: {names}"
        assert all(u.user_type == "subscriber" for u in items)


def test_explicit_user_type_card_returns_cards(app):
    with app.app_context():
        _seed_one(1, "ahmad", user_type="subscriber")
        _seed_one(1, "12345678", user_type="card")
        _seed_one(1, "87654321", user_type="card")

        from app.radius.services.users import get_users_service
        cards = list(get_users_service().list(user_type="card", limit=100))
        usernames = sorted(c.username for c in cards)
        assert usernames == ["12345678", "87654321"]


def test_user_type_none_returns_everything(app):
    with app.app_context():
        _seed_one(1, "ahmad", user_type="subscriber")
        _seed_one(1, "12345678", user_type="card")

        from app.radius.services.users import get_users_service
        # explicit None disables the filter for callers that need it
        items = list(get_users_service().list(user_type=None, limit=100))
        assert {u.username for u in items} == {"ahmad", "12345678"}


def test_search_pushdown_finds_user_buried_past_limit(app):
    """The actual bug: 2057 subscribers + page limit=1000 → real 'ahmad'
    at id=37 (~row 2020 by descending id) was filtered after LIMIT in
    Python and never appeared. With SQL pushdown it's found regardless
    of how deep it is."""
    with app.app_context():
        # ahmad first → low id
        _seed_one(1, "ahmad", user_type="subscriber", full_name="Ahmad")
        # then 50 card-mirrors with higher ids (would push ahmad past LIMIT
        # if limit were tiny)
        for i in range(50):
            _seed_one(1, f"{i:08d}", user_type="card")

        from app.radius.services.users import get_users_service
        # Even with a tiny limit, search reaches ahmad because LIKE
        # runs BEFORE LIMIT.
        found = list(get_users_service().list(
            user_type=None, search="ahmad", limit=5))
        usernames = [u.username for u in found]
        assert "ahmad" in usernames, \
            f"search didn't push down past LIMIT: {usernames}"


def test_search_within_default_subscriber_filter(app):
    """Search + default user_type='subscriber' together — must still
    exclude cards even if their username matches the search."""
    with app.app_context():
        _seed_one(1, "ahmad", user_type="subscriber",
                  full_name="Ahmad Test")
        _seed_one(1, "ahmad_card", user_type="card")  # matches search but is card

        from app.radius.services.users import get_users_service
        items = list(get_users_service().list(search="ahmad", limit=100))
        usernames = [u.username for u in items]
        assert usernames == ["ahmad"]
