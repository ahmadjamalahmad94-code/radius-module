# -*- coding: utf-8 -*-
"""Bug #3 — safe reconcile of migrated cards' from-first-connection expiry.

Pins the safety contract: fixes the stale-expiry cards (card 5698046 example),
is idempotent, never shortens a genuinely-fine card, only touches migrated
(source_type='imported') batches, and is fully reversible.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest

from app.radius.services import card_time_reconcile as rec


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_rec_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _iso(dt):
    return dt.isoformat() if dt else None


def _seed(app, *, username, source_type="imported", count_from_first_connect=True,
          time_value=3, time_unit="hours", first_used_at=None, expire_at=None,
          add_subscriber=True, add_radacct=False):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _dt.datetime.utcnow().isoformat()
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, "
                "created_at) VALUES (1,?,?,?)",
                ("4 ميجا فري لانسر", "Hotspot", now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                "package_name, count_from_first_connect, time_value, time_unit, "
                "source_type, created_at) VALUES (1,?,?,?,?,?,?,?,?,?)",
                ("b-" + username, pid, 1, "امواج البحر",
                 1 if count_from_first_connect else 0, time_value, time_unit,
                 source_type, now)).lastrowid
            cid = c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, "
                "plan_id, used, first_used_at, expire_at, created_at) "
                "VALUES (1,?,?,?,?,?,?,?,?)",
                (bid, username, "pw", pid, 1 if first_used_at else 0,
                 _iso(first_used_at), _iso(expire_at), now)).lastrowid
            if add_subscriber:
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, password, "
                    "expire_at, created_at) VALUES (1,?,?,?,?)",
                    (username, "pw", _iso(expire_at), now))
            if add_radacct and first_used_at:
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                    "username, acctstarttime) VALUES (1,?,?,?,?)",
                    ("s-" + username, "u-" + username, username,
                     _iso(first_used_at)))
    return cid, bid


def test_reconcile_fixes_stale_expiry_idempotent_and_reversible(app):
    """Card 5698046: from-first-connect batch «امواج البحر» (3h), connected 1h
    ago, but carries a stale generation-time expiry 90 days in the past →
    remaining shows 0. Reconcile extends the expiry to first_connect + 3h."""
    first = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    stale = _dt.datetime.utcnow() - _dt.timedelta(days=90)
    cid, _ = _seed(app, username="5698046", first_used_at=first, expire_at=stale)
    now = _dt.datetime.utcnow()
    with app.app_context():
        from app.radius.db.connection import db
        plan = rec.plan_reconcile(db(), 1, now=now)
        assert len(plan.to_fix) == 1
        d = plan.to_fix[0]
        assert d.card_id == cid
        assert d.reason == "stale_past_expiry"
        assert d.remaining_old == 0
        assert 7100 <= d.remaining_new <= 7200
        target = rec._parse_dt(d.new_expire_at)
        assert abs((target - (first + _dt.timedelta(hours=3))).total_seconds()) < 2

        res = rec.apply_reconcile(db(), plan)
        assert res["applied"] == 1
        undo = res["undo"]

        # cards + subscribers both updated to the correct expiry.
        crow = db().execute("SELECT expire_at FROM cards WHERE id=?", (cid,)).fetchone()
        srow = db().execute(
            "SELECT expire_at FROM subscribers WHERE username='5698046'").fetchone()
        assert rec._parse_dt(crow["expire_at"]) > now
        assert crow["expire_at"] == srow["expire_at"]

        # Idempotent: a second plan finds nothing to fix (now genuinely fine).
        plan2 = rec.plan_reconcile(db(), 1, now=now)
        assert len(plan2.to_fix) == 0
        assert plan2.decisions[0].reason == "genuinely_fine"

        # Reversible: revert restores the original stale expiry.
        rec.revert(db(), 1, undo)
        back = db().execute("SELECT expire_at FROM cards WHERE id=?", (cid,)).fetchone()
        assert rec._parse_dt(back["expire_at"]) == stale


def test_reconcile_never_shortens_a_card_with_more_time(app):
    """A card whose current expiry is FAR in the future (more than the budget)
    must be left untouched — the fix only ever extends time."""
    first = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    future = _dt.datetime.utcnow() + _dt.timedelta(days=10)
    _seed(app, username="fine-1", first_used_at=first, expire_at=future)
    with app.app_context():
        from app.radius.db.connection import db
        plan = rec.plan_reconcile(db(), 1, now=_dt.datetime.utcnow())
        assert len(plan.to_fix) == 0
        assert plan.decisions[0].action == rec.ACTION_KEEP
        assert plan.decisions[0].reason == "genuinely_fine"


def test_reconcile_leaves_unlimited_card_untouched(app):
    """A connected from-first-connect card with a NULL (unlimited) expiry is
    treated as +inf remaining → never shortened to a finite budget."""
    first = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    _seed(app, username="unl-1", first_used_at=first, expire_at=None)
    with app.app_context():
        from app.radius.db.connection import db
        plan = rec.plan_reconcile(db(), 1, now=_dt.datetime.utcnow())
        assert len(plan.to_fix) == 0
        assert plan.decisions[0].reason == "genuinely_fine"


def test_reconcile_skips_by_seconds_batch(app):
    first = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    stale = _dt.datetime.utcnow() - _dt.timedelta(days=90)
    _seed(app, username="bysec-1", count_from_first_connect=False,
          first_used_at=first, expire_at=stale)
    with app.app_context():
        from app.radius.db.connection import db
        plan = rec.plan_reconcile(db(), 1, now=_dt.datetime.utcnow())
        assert len(plan.to_fix) == 0
        assert plan.decisions[0].reason == "not_from_first_connect"


def test_reconcile_ignores_non_migrated_batches(app):
    """A generated (non-migrated) batch is never a candidate, even if stale."""
    first = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    stale = _dt.datetime.utcnow() - _dt.timedelta(days=90)
    _seed(app, username="gen-1", source_type="generated",
          first_used_at=first, expire_at=stale)
    with app.app_context():
        from app.radius.db.connection import db
        plan = rec.plan_reconcile(db(), 1, now=_dt.datetime.utcnow())
        assert len(plan.decisions) == 0


def test_reconcile_uses_radacct_when_first_used_at_missing(app):
    """When cards.first_used_at is NULL (native rlm_sql auth), the first
    connection is recovered from radacct — and the stale card gets fixed."""
    first = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
    stale = _dt.datetime.utcnow() - _dt.timedelta(days=90)
    _seed(app, username="viaacct-1", first_used_at=None, expire_at=stale,
          add_radacct=False)
    # Insert the card with no first_used_at but a radacct session start.
    with app.app_context():
        from app.radius.db.connection import db, transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                "username, acctstarttime) VALUES (1,?,?,?,?)",
                ("s-va", "u-va", "viaacct-1", first.isoformat()))
        plan = rec.plan_reconcile(db(), 1, now=_dt.datetime.utcnow())
        assert len(plan.to_fix) == 1
        assert plan.to_fix[0].reason == "stale_past_expiry"


def test_reconcile_not_connected_card_is_not_touched(app):
    """A from-first-connect card that never connected has no first connection;
    reconcile leaves it (arms at first login) rather than erasing/setting."""
    stale = _dt.datetime.utcnow() - _dt.timedelta(days=90)
    _seed(app, username="never-1", first_used_at=None, expire_at=stale)
    with app.app_context():
        from app.radius.db.connection import db
        plan = rec.plan_reconcile(db(), 1, now=_dt.datetime.utcnow())
        assert len(plan.to_fix) == 0
        assert plan.decisions[0].reason == "not_connected"
