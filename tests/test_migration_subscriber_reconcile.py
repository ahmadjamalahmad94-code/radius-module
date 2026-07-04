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


# ═══════════ REAL adv/Hobe-Hub shape (proven live from the client dump) ═════
# Subscribers live in radcheck (is_card=0) + userinfo; the REAL fields are
# userinfo.internet_status (enum enabled/disabled) and userinfo.exp_time
# (epoch). The live bug: expire_at was semantically mis-captured from
# userinfo.creationdate (always past → everyone «منتهي», stomping «معطّل»).

def _adv_real_shape_bytes(*, exp_time_active, exp_time_expired,
                          disabled_status="disabled"):
    import time
    return _sqlite_bytes(f"""
        CREATE TABLE radcheck (id INTEGER, username TEXT, attribute TEXT,
                               op TEXT, value TEXT, is_card INTEGER);
        INSERT INTO radcheck VALUES
          (1,'u-active','Cleartext-Password',':=','pw',0),
          (2,'u-expired','Cleartext-Password',':=','pw',0),
          (3,'u-disabled','Cleartext-Password',':=','pw',0);
        CREATE TABLE userinfo (id INTEGER, username TEXT, firstname TEXT,
                               creationdate INTEGER, exp_time INTEGER,
                               internet_status TEXT);
        INSERT INTO userinfo VALUES
          (1,'u-active','A',1600000000,{exp_time_active},'enabled'),
          (2,'u-expired','B',1600000000,{exp_time_expired},'enabled'),
          (3,'u-disabled','C',1600000000,{exp_time_expired},'{disabled_status}');
    """)


def test_real_shape_exp_time_drives_expired_not_creationdate(app_ctx):
    # u-active: future exp_time → enabled. u-expired: past exp_time → expired.
    # If creationdate (always past) were still captured as expire_at, u-active
    # would wrongly import as expired too.
    import time
    now = int(time.time())
    from app.radius.services.migration import engine
    res = engine.analyze(_adv_real_shape_bytes(
        exp_time_active=now + 90 * 86400, exp_time_expired=now - 90 * 86400),
        "adv.db")
    engine.commit(TID, res.dataset, res.matches, dry_run=False)
    assert _status_of("u-active") == "enabled"      # future exp_time
    assert _status_of("u-expired") == "expired"     # past exp_time
    # internet_status='disabled' outranks the past expiry → disabled.
    assert _status_of("u-disabled") == "disabled"


def test_reconcile_never_downgrades_disabled_to_expired(app_ctx):
    # The live complaint: «المشتركين المعطلين حاططهم منتهي اشتراكهم مش معطل».
    # A DB-disabled subscriber whose source row is enabled + past expiry must
    # STAY disabled after a reconcile re-import (block outranks expiry).
    import time
    now = int(time.time())
    from app.radius.services.migration import engine
    res = engine.analyze(_adv_real_shape_bytes(
        exp_time_active=now + 90 * 86400, exp_time_expired=now - 90 * 86400),
        "adv.db")
    engine.commit(TID, res.dataset, res.matches, dry_run=False)
    # Admin manually disables u-expired in the NEW panel.
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute("UPDATE subscribers SET status='disabled' "
                  "WHERE tenant_id=? AND username='u-expired'", (TID,))
    # Re-upload the same backup (reconcile): source says enabled + past expiry.
    res2 = engine.analyze(_adv_real_shape_bytes(
        exp_time_active=now + 90 * 86400, exp_time_expired=now - 90 * 86400),
        "adv.db")
    engine.commit(TID, res2.dataset, res2.matches, dry_run=False)
    assert _status_of("u-expired") == "disabled", \
        "a blocked subscriber must never be downgraded to expired"
    # And the genuinely-expired logic still works for others… u-disabled stays.
    assert _status_of("u-disabled") == "disabled"
    assert _status_of("u-active") == "enabled"


def _adv_block_pool_bytes(*, future_exp, past_exp):
    """radcheck carries the REAL adv disable mechanism: framed_pool='block'
    (the user is thrown into the block pool). internet_status stays 'enabled'
    (the default) — the block pool is the only disable signal."""
    return _sqlite_bytes(f"""
        CREATE TABLE radcheck (id INTEGER, username TEXT, attribute TEXT,
                               op TEXT, value TEXT, is_card INTEGER,
                               address_list_name TEXT, framed_pool TEXT);
        INSERT INTO radcheck VALUES
          (1,'u-ok','Cleartext-Password',':=','pw',0,'',''),
          (2,'u-blocked-future','Cleartext-Password',':=','pw',0,'','block'),
          (3,'u-blocked-past','Cleartext-Password',':=','pw',0,'','block'),
          (4,'card-blocked','Cleartext-Password',':=','pw',1,'','block');
        CREATE TABLE userinfo (id INTEGER, username TEXT, firstname TEXT,
                               creationdate INTEGER, exp_time INTEGER,
                               internet_status TEXT);
        INSERT INTO userinfo VALUES
          (1,'u-ok','A',1600000000,{future_exp},'enabled'),
          (2,'u-blocked-future','B',1600000000,{future_exp},'enabled'),
          (3,'u-blocked-past','C',1600000000,{past_exp},'enabled');
    """)


def test_block_pool_imports_as_disabled(app_ctx):
    # The REAL adv disable: framed_pool='block'. A blocked subscriber imports
    # as DISABLED — even with a future expiry (146/147 in the live dump), and
    # even with a past expiry (block outranks expiry: «معطّل» not «منتهي»).
    import time
    now = int(time.time())
    from app.radius.services.migration import engine
    res = engine.analyze(_adv_block_pool_bytes(
        future_exp=now + 90 * 86400, past_exp=now - 90 * 86400), "adv.db")
    engine.commit(TID, res.dataset, res.matches, dry_run=False)
    assert _status_of("u-ok") == "enabled"
    assert _status_of("u-blocked-future") == "disabled"
    assert _status_of("u-blocked-past") == "disabled"   # block outranks expiry


def test_block_pool_reconcile_fixes_wrongly_active(app_ctx):
    # Live scenario: a blocked source user was previously imported as enabled/
    # expired (block signal unread). Re-upload in reconcile mode → disabled.
    import time
    now = int(time.time())
    from app.radius.services.migration import engine
    res = engine.analyze(_adv_block_pool_bytes(
        future_exp=now + 90 * 86400, past_exp=now - 90 * 86400), "adv.db")
    engine.commit(TID, res.dataset, res.matches, dry_run=False)
    # Simulate the pre-fix wrong state.
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute("UPDATE subscribers SET status='enabled' "
                  "WHERE tenant_id=? AND username='u-blocked-future'", (TID,))
        c.execute("UPDATE subscribers SET status='expired' "
                  "WHERE tenant_id=? AND username='u-blocked-past'", (TID,))
    res2 = engine.analyze(_adv_block_pool_bytes(
        future_exp=now + 90 * 86400, past_exp=now - 90 * 86400), "adv.db")
    engine.commit(TID, res2.dataset, res2.matches, dry_run=False)
    assert _status_of("u-blocked-future") == "disabled"
    assert _status_of("u-blocked-past") == "disabled"
    assert _status_of("u-ok") == "enabled"
