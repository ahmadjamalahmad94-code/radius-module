"""FIX 3 regression: SAFE reconcile tool for already-imported cards/batches.

The owner's live cards were imported with a stale generation-time expire_at
(often in the past). This tool re-derives each from-first-connect card's
correct first-connection expiry from its BATCH and moves the enforced
cards.expire_at / subscribers.expire_at FORWARD to match.

Guarantees exercised here:
  • dry-run (plan) mutates NOTHING.
  • apply is EXTEND-ONLY — never shortens a genuinely-fine card.
  • apply is IDEMPOTENT — a second run changes nothing.
  • apply refuses without allow_apply=True (backup-gated at the route).
  • verify on card 5698046 (batch «امواج البحر», 3h, first-connect ~1h ago)
    → remaining ≈ 2h and expire_at = first_connect + 3h.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_fix3_")
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


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed(conn, *, username, count_from_first_connect=1, time_value=3,
          time_unit="hours", first_connect_dt=None, card_expire=None,
          with_subscriber=False):
    now = _iso(datetime.utcnow())
    conn.execute(
        "INSERT INTO access_plans (tenant_id, name, enabled, created_at) "
        "VALUES (1, 'p', 1, ?)", (now,))
    plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO card_batches
            (tenant_id, batch_code, package_name, plan_id, count, generated, used,
             count_from_first_connect, count_by_seconds, time_value, time_unit,
             created_by, status, created_at, metadata)
        VALUES (1, ?, 'امواج البحر', ?, 1, 1, 0, ?, 0, ?, ?, 'seed', 'active', ?, '{}')
        """,
        (f"B-{username}", plan_id, count_from_first_connect, time_value,
         time_unit, now),
    )
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    sub_id = None
    if with_subscriber:
        conn.execute(
            "INSERT INTO subscribers (tenant_id, username, expire_at, created_at) "
            "VALUES (1, ?, ?, ?)", (username, _iso(card_expire) if card_expire else None, now))
        sub_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id, used,
             first_used_at, expire_at, used_by_subscriber_id, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 1, ?, ?, ?, 0, ?)
        """,
        (batch_id, username, plan_id,
         _iso(first_connect_dt) if first_connect_dt else None,
         _iso(card_expire) if card_expire else None, sub_id, now),
    )
    return batch_id, sub_id


def _card_expire(app, username):
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT expire_at FROM cards WHERE tenant_id=1 AND username=?",
            (username,)).fetchone()
        return row["expire_at"] if row else None


def test_plan_is_dry_run_no_mutation(app):
    first = datetime.utcnow() - timedelta(hours=1)
    stale = datetime.utcnow() - timedelta(days=90)  # generation-time, in the past
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="5698046", first_connect_dt=first, card_expire=stale)
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        rp = get_card_accounting_reconcile_service().plan(1)

    # The plan proposes exactly one card update…
    assert len(rp.card_updates) == 1
    # …but the DB row is untouched (still the stale expiry).
    assert _card_expire(app, "5698046") == _iso(stale)


def test_apply_fixes_5698046_to_2h_remaining(app):
    first = datetime.utcnow() - timedelta(hours=1)
    stale = datetime.utcnow() - timedelta(days=90)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="5698046", first_connect_dt=first, card_expire=stale,
                  with_subscriber=True)
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        report = get_card_accounting_reconcile_service().apply(1, "owner", allow_apply=True)

    assert report["applied_cards"] == 1
    # expire_at is now first_connect + 3h.
    from app.radius.db.helpers import parse_dt
    new_expire = parse_dt(_card_expire(app, "5698046"))
    expected = first + timedelta(hours=3)
    assert abs((new_expire - expected).total_seconds()) < 2
    # remaining ≈ 2h from now.
    rem = (new_expire - datetime.utcnow()).total_seconds()
    assert 2 * 3600 - 180 <= rem <= 2 * 3600
    # subscriber expiry moved forward too.
    with app.app_context():
        from app.radius.db.connection import db
        srow = db().execute(
            "SELECT expire_at FROM subscribers WHERE tenant_id=1 AND username=?",
            ("5698046",)).fetchone()
        assert parse_dt(srow["expire_at"]) == new_expire


def test_apply_is_extend_only_never_shortens(app):
    # A genuinely-fine card whose expiry is already BEYOND first_connect+budget
    # must NOT be shortened.
    first = datetime.utcnow() - timedelta(hours=1)
    far_future = datetime.utcnow() + timedelta(days=365)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="fine-card", first_connect_dt=first, card_expire=far_future)
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        report = get_card_accounting_reconcile_service().apply(1, "owner", allow_apply=True)

    assert report["applied_cards"] == 0
    from app.radius.db.helpers import parse_dt
    assert parse_dt(_card_expire(app, "fine-card")) == far_future


def test_apply_is_idempotent(app):
    first = datetime.utcnow() - timedelta(hours=1)
    stale = datetime.utcnow() - timedelta(days=90)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="5698046", first_connect_dt=first, card_expire=stale)
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        svc = get_card_accounting_reconcile_service()
        r1 = svc.apply(1, "owner", allow_apply=True)
        after_first = _card_expire(app, "5698046")
        r2 = svc.apply(1, "owner", allow_apply=True)
        after_second = _card_expire(app, "5698046")

    assert r1["applied_cards"] == 1
    assert r2["applied_cards"] == 0        # nothing left to do
    assert after_first == after_second     # unchanged on the second run


def test_apply_refuses_without_allow_flag(app):
    with app.app_context():
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        with pytest.raises(PermissionError):
            get_card_accounting_reconcile_service().apply(1, "owner")


def test_not_connected_card_is_left_alone(app):
    # A from-first-connect card that never connected has no first connection —
    # its expiry is materialised on first connect, not by this tool.
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="never-used", first_connect_dt=None, card_expire=None)
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        rp = get_card_accounting_reconcile_service().plan(1)

    assert len(rp.card_updates) == 0
    assert rp.skipped.get("not_connected", 0) == 1


def test_by_seconds_card_is_not_touched(app):
    first = datetime.utcnow() - timedelta(hours=1)
    stale = datetime.utcnow() - timedelta(days=90)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="bysec", count_from_first_connect=0,
                  first_connect_dt=first, card_expire=stale)
        from app.radius.services.card_accounting_reconcile import (
            get_card_accounting_reconcile_service)
        rp = get_card_accounting_reconcile_service().plan(1)

    assert len(rp.card_updates) == 0
    assert rp.skipped.get("not_from_first_connect", 0) == 1
