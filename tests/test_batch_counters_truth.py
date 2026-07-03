# -*- coding: utf-8 -*-
"""Batch-card counters must be truthful — «الكروت 2000 والمتصل الآن 2000 مش منطقي».

Live bug on /admin/radius/cards/batches: a freshly-generated 2000-card batch
showed «نشطة الآن 2000». Root cause: acct_stats counted
`CASE WHEN r.acctstoptime IS NULL` over a LEFT JOIN to radacct — a card with
ZERO sessions produces one NULL-extended join row whose acctstoptime IS NULL,
so EVERY never-used card counted as one live session. Also no liveness window,
so zombie open rows counted forever.

Fixed semantics per batch (the owner's six numbers):
  total_cards      كم عدد الكروت
  online_cards_now كم متصل الآن   (real radacct row + open + live window)
  used_count       كم تم استخدام  (ever-used, any current state)
  available_count  كم باقٍ متاح   (never used, not expired, not revoked)
  expired_count    كم منتهٍ
  active_count - online_cards_now  مستخدمة سارية غير متصلة (template-derived)
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_batchcnt_")
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


def _iso(minutes_ago: int = 0) -> str:
    return (_dt.datetime.utcnow()
            - _dt.timedelta(minutes=minutes_ago)).isoformat() + "Z"


def _seed(app) -> int:
    """One batch, one plan, four cards:
      c-live   used, LIVE open session          → online
      c-zombie used, STALE open session (2h old) → NOT online
      c-fresh  never used, NO radacct rows       → NOT online (the 2000-bug)
      c-exp    used but expired yesterday        → expired, NOT online
    Returns the batch id."""
    from app.radius.db.connection import transaction
    now = _iso()
    yesterday = (_dt.datetime.utcnow()
                 - _dt.timedelta(days=1)).isoformat() + "Z"
    with transaction() as c:
        c.execute("INSERT INTO access_plans (tenant_id, name, created_at) "
                  "VALUES (1, 'p', ?)", (now,))
        plan_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        c.execute("INSERT INTO card_batches (tenant_id, batch_code, plan_id, "
                  "count, generated, created_at) VALUES (1, 'B-T', ?, 4, 4, ?)",
                  (plan_id, now))
        batch_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

        def card(u, used=0, expire=None):
            c.execute(
                "INSERT INTO cards (tenant_id, batch_id, username, password, "
                "plan_id, used, revoked, expire_at, created_at) "
                "VALUES (1,?,?,?,?,?,0,?,?)",
                (batch_id, u, "p", plan_id, used, expire, now))

        card("c-live", used=1)
        card("c-zombie", used=1)
        card("c-fresh", used=0)
        card("c-exp", used=1, expire=yesterday)

        def sess(u, sid, minutes_ago):
            c.execute(
                "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, acctstarttime, acctupdatetime, "
                "acctstoptime) VALUES (1,?,?,?,?,?,?,NULL)",
                (sid, sid + "u", u, "10.0.0.1",
                 _iso(minutes_ago), _iso(minutes_ago)))

        sess("c-live", "s-live", 1)          # within the live window
        sess("c-zombie", "s-zombie", 120)    # stale open row (zombie)
    return int(batch_id)


def _batch_row(app, batch_id):
    from app.radius.db.repos import cards_repo
    rows = cards_repo.list_batch_operations(1, limit=10)
    return next(r for r in rows if int(r["id"]) == batch_id)


def test_online_now_counts_only_genuinely_live_cards(app):
    """The reported absurdity: never-used cards must NOT count as connected
    (LEFT JOIN NULL trap), and zombie open rows must NOT count (window)."""
    with app.app_context():
        bid = _seed(app)
        b = _batch_row(app, bid)
    assert b["total_cards"] == 4
    assert b["online_cards_now"] == 1, \
        "only c-live is genuinely connected — not the fresh 2000-bug cards"
    # the raw live-session count agrees (one live session)
    assert b["online_sessions"] == 1


def test_owner_six_numbers_are_consistent(app):
    with app.app_context():
        bid = _seed(app)
        b = _batch_row(app, bid)
    assert b["total_cards"] == 4          # كم عدد الكروت
    assert b["online_cards_now"] == 1     # كم متصل الآن
    assert b["used_count"] == 3           # كم تم استخدام (live+zombie+expired)
    assert b["available_count"] == 1      # كم باقٍ متاح (c-fresh)
    assert b["expired_count"] == 1        # كم منتهٍ (c-exp)
    # مستخدمة سارية غير متصلة = active (used & not expired) - online = 2-1
    assert b["active_count"] - b["online_cards_now"] == 1
    # sanity: buckets tile the batch: available + used == total (no revoked)
    assert b["available_count"] + b["used_count"] == b["total_cards"]


def test_fresh_batch_shows_zero_online(app):
    """A batch of never-used cards (the owner's 2000-card case) must show 0
    connected — the old query showed total_cards."""
    from app.radius.db.connection import transaction
    with app.app_context():
        now = _iso()
        with transaction() as c:
            c.execute("INSERT INTO access_plans (tenant_id, name, created_at) "
                      "VALUES (1, 'p2', ?)", (now,))
            plan_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
            c.execute("INSERT INTO card_batches (tenant_id, batch_code, plan_id, "
                      "count, generated, created_at) VALUES (1,'B-F',?,50,50,?)",
                      (plan_id, now))
            bid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
            for i in range(50):
                c.execute(
                    "INSERT INTO cards (tenant_id, batch_id, username, password, "
                    "plan_id, used, revoked, created_at) VALUES (1,?,?,?,?,0,0,?)",
                    (bid, f"f{i}", "p", plan_id, now))
        b = _batch_row(app, bid)
    assert b["total_cards"] == 50
    assert b["online_cards_now"] == 0
    assert b["online_sessions"] == 0
    assert b["used_count"] == 0
    assert b["available_count"] == 50
