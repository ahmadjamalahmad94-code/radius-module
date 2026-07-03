"""Integration: migration imports subscriber STATUS + EXPIRY, and re-import in
reconcile (merge) mode fixes drifted rows safely.

Covers the owner's bug (source has 477 expired, migration made everyone active)
and the reconcile-re-upload path:
  • a source-expired subscriber imports as 'expired' (not 'active').
  • a disabled/blocked source subscriber imports as 'disabled'.
  • re-import (merge) is idempotent — no duplicate rows, stable statuses.
  • a DB record absent from the re-uploaded source is left untouched (not deleted).
  • a manually-disabled subscriber is NOT un-blocked without explicit source enable.
  • card batch accounting budget + count_from_first_connect refresh on reconcile.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "migration.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        yield app
    reset_for_tests(None)


TID = 1


def _sqlite_bytes(script: str) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript(script)
        c.commit()
        c.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


# A plain subscribers table with status + expiry columns. active-future is
# genuinely active; expired-past has a PAST expiry but source status 'active'
# (the exact «كله فعّال» trap); disabled-user is blocked in the source.
_SUBS = """
    CREATE TABLE plans (id INTEGER, name TEXT, price REAL);
    INSERT INTO plans VALUES (1,'Gold',10);
    CREATE TABLE subscribers (id INTEGER, username TEXT, password TEXT,
                              plan TEXT, expire_at TEXT, status TEXT);
    INSERT INTO subscribers VALUES
      (1,'active-future','pw','Gold','2030-01-01','active'),
      (2,'expired-past','pw','Gold','2020-01-01','active'),
      (3,'disabled-user','pw','Gold','2030-01-01','disabled');
"""


def _commit(app):
    from app.radius.services.migration import engine
    res = engine.analyze(_sqlite_bytes(_SUBS), "src.db")
    return engine.commit(TID, res.dataset, res.matches, dry_run=False)


def _status_of(username):
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT status FROM subscribers WHERE tenant_id=? AND username=?",
        (TID, username)).fetchone()
    return row["status"] if row else None


def _count():
    from app.radius.db.connection import db
    return int(db().execute(
        "SELECT COUNT(*) AS c FROM subscribers WHERE tenant_id=?",
        (TID,)).fetchone()["c"])


def test_import_maps_status_and_expiry(app_ctx):
    _commit(app_ctx)
    assert _status_of("active-future") == "enabled"
    # The crux: source said 'active' but expiry is in the past → expired.
    assert _status_of("expired-past") == "expired"
    assert _status_of("disabled-user") == "disabled"


def test_reimport_reconcile_is_idempotent_no_duplicates(app_ctx):
    _commit(app_ctx)
    n1 = _count()
    _commit(app_ctx)                       # re-upload the same backup (merge)
    n2 = _count()
    assert n1 == n2 == 3                    # no new rows on re-run
    # Statuses unchanged on re-run.
    assert _status_of("expired-past") == "expired"
    assert _status_of("disabled-user") == "disabled"


def test_absent_record_is_left_untouched_not_deleted(app_ctx):
    _commit(app_ctx)
    # A subscriber that exists in the DB but is ABSENT from the re-uploaded
    # source must survive reconcile untouched (reconcile never deletes).
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO subscribers(tenant_id, username, password, status, "
            "created_at) VALUES (?, 'manual-only', 'pw', 'enabled', "
            "datetime('now'))", (TID,))
    _commit(app_ctx)                       # re-import: source has no manual-only
    assert _status_of("manual-only") == "enabled"   # still there


def test_db_disabled_not_unblocked_without_explicit_enable(app_ctx):
    # Import, then an admin manually disables a subscriber. Re-uploading a
    # source where that user has NO status column value and a future expiry
    # must NOT un-block them (block persists). We simulate "no status signal"
    # by importing a source whose status cell is blank for that user.
    _commit(app_ctx)
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute("UPDATE subscribers SET status='disabled' "
                  "WHERE tenant_id=? AND username='active-future'", (TID,))
    # Re-upload a source with a BLANK status for active-future (future expiry).
    src = """
        CREATE TABLE plans (id INTEGER, name TEXT, price REAL);
        INSERT INTO plans VALUES (1,'Gold',10);
        CREATE TABLE subscribers (id INTEGER, username TEXT, password TEXT,
                                  plan TEXT, expire_at TEXT, status TEXT);
        INSERT INTO subscribers VALUES
          (1,'active-future','pw','Gold','2030-01-01','');
    """
    from app.radius.services.migration import engine
    res = engine.analyze(_sqlite_bytes(src), "src2.db")
    engine.commit(TID, res.dataset, res.matches, dry_run=False)
    # No explicit enable in source → block persists.
    assert _status_of("active-future") == "disabled"


def test_card_batch_reconcile_corrects_stale_month_to_3h(app_ctx):
    # Live symptom: batch «امواج البحر» shows «مدة البطاقة: 1 شهر». Seed exactly
    # that (time_value=1, time_unit='months'), then run _commit_batch in merge
    # mode with the source-derived budget (3h, from-first-connect) → the stale
    # month is CORRECTED to 3 hours on the existing batch.
    from app.radius.db.connection import db, transaction
    from app.radius.services.migration import engine
    from app.radius.services.migration.model import Candidate
    from app.radius.services.migration.sections import SEC_BATCHES, norm_key
    with transaction() as c:
        pid = c.execute(
            "INSERT INTO access_plans(tenant_id, name, enabled, created_at) "
            "VALUES (?, 'p', 1, datetime('now'))", (TID,)).lastrowid
        bid = c.execute(
            "INSERT INTO card_batches(tenant_id, batch_code, package_name, "
            "plan_id, count, generated, used, time_value, time_unit, "
            "count_from_first_connect, created_by, status, created_at, metadata) "
            "VALUES (?, 'B1', 'امواج البحر', ?, 0, 0, 0, 1, 'months', 0, 'seed', "
            "'active', datetime('now'), '{}')", (TID, pid)).lastrowid
    idmap = {SEC_BATCHES: {norm_key("امواج البحر"): int(bid)}}
    cand = Candidate(
        section=SEC_BATCHES, natural_key=norm_key("امواج البحر"),
        fields={"name": "امواج البحر", "time_value": 3, "time_unit": "hours",
                "count_from_first_connect": True},
        source_ref="امواج البحر")
    engine._commit_batch(TID, cand, "merge", idmap, "tester", False)
    row = db().execute(
        "SELECT time_value, time_unit, count_from_first_connect "
        "FROM card_batches WHERE tenant_id=? AND id=?", (TID, bid)).fetchone()
    assert (row["time_value"], row["time_unit"]) == (3, "hours")   # not 1 month
    assert int(row["count_from_first_connect"]) == 1
    # And that budget equals 3h = 10800s (drives the checker's remaining time).
    from app.radius.services.card_accounting import budget_seconds
    assert budget_seconds(time_value=row["time_value"],
                          time_unit=row["time_unit"]) == 10800
