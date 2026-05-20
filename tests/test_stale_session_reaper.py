"""R12.1 regression: stale radacct rows must be auto-closed.

MikroTik does not reliably send Acct-Stop in several cases (client device
vanishes, NAS reboot without Acct-On, lost UDP, etc.). Without a reaper,
those rows stay open forever and clog /admin/radius/online with phantom
"connected" users.

The reaper closes any row where the last sign of life (acctupdatetime,
falling back to acctstarttime) is older than the configured threshold.
We test the pure SQL-level `reap_once` here — the loop/thread is just
`while True: reap_once; sleep(interval)`, no logic to test.

Coverage:
 1. A row whose last update is OLDER than threshold gets closed, with
    acctstoptime = the last known activity time (NOT now).
 2. A row whose last update is RECENT stays open.
 3. A row with NO acctupdatetime falls back to acctstarttime.
 4. Already-closed rows are left untouched (idempotent).
 5. Terminate cause is 'Stale-Session-Timeout' on closed rows.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r121_")
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
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed(conn, *, username, started_min_ago, updated_min_ago=None,
          stopped=False, session_id=None):
    """Insert a radacct row with controllable acctstarttime / acctupdatetime."""
    start = datetime.utcnow() - timedelta(minutes=started_min_ago)
    upd = (datetime.utcnow() - timedelta(minutes=updated_min_ago)
           if updated_min_ago is not None else None)
    stop = datetime.utcnow() if stopped else None
    sid = session_id or f"s-{username}-{started_min_ago}"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, acctstarttime, acctupdatetime, acctstoptime)
        VALUES (?,?,?,?,?,?,?,?)
    """, (1, sid, f"u-{sid}", username, "10.10.0.2",
           _iso(start), _iso(upd) if upd else None,
           _iso(stop) if stop else None))


def test_reaper_closes_row_with_old_last_update(app):
    """20-minute-old interim update + threshold=15min → closed."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.stale_session_reaper import reap_once

        with transaction() as c:
            _seed(c, username="ghost", started_min_ago=60, updated_min_ago=20)

        n = reap_once(threshold_sec=15 * 60)
        assert n == 1

        row = db().execute(
            "SELECT acctstoptime, acctterminatecause FROM radacct "
            "WHERE username='ghost'"
        ).fetchone()
        assert row["acctstoptime"] is not None
        assert row["acctterminatecause"] == "Stale-Session-Timeout"


def test_reaper_uses_last_activity_not_now_as_stoptime(app):
    """acctstoptime should be the last known activity, so reported session
    duration stays honest (not inflated by the 15min we waited)."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.stale_session_reaper import reap_once

        with transaction() as c:
            _seed(c, username="ghost", started_min_ago=60, updated_min_ago=20)

        reap_once(threshold_sec=15 * 60)

        row = db().execute(
            "SELECT acctstarttime, acctupdatetime, acctstoptime FROM radacct "
            "WHERE username='ghost'"
        ).fetchone()
        # stoptime should equal acctupdatetime (the last sign of life),
        # NOT the moment we ran the reaper.
        assert row["acctstoptime"] == row["acctupdatetime"]


def test_reaper_leaves_recent_row_alone(app):
    """A row that received interim 2 minutes ago is alive — don't touch."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.stale_session_reaper import reap_once

        with transaction() as c:
            _seed(c, username="alive", started_min_ago=10, updated_min_ago=2)

        n = reap_once(threshold_sec=15 * 60)
        assert n == 0

        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE username='alive'"
        ).fetchone()
        assert row["acctstoptime"] is None


def test_reaper_falls_back_to_starttime_when_no_interim(app):
    """If a row never got an interim update, use acctstarttime to judge
    staleness — a 1-hour-old session with no interim is definitely dead."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.stale_session_reaper import reap_once

        with transaction() as c:
            _seed(c, username="no-interim", started_min_ago=60,
                   updated_min_ago=None)

        n = reap_once(threshold_sec=15 * 60)
        assert n == 1

        row = db().execute(
            "SELECT acctstarttime, acctstoptime FROM radacct "
            "WHERE username='no-interim'"
        ).fetchone()
        # fallback: stoptime = starttime when no interim ever happened
        assert row["acctstoptime"] == row["acctstarttime"]


def test_reaper_skips_already_closed_rows(app):
    """Idempotent — running twice shouldn't change anything on the
    second pass, and a row that was closed normally must not be re-touched."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.stale_session_reaper import reap_once

        with transaction() as c:
            _seed(c, username="finished", started_min_ago=60,
                   updated_min_ago=30, stopped=True)

        n1 = reap_once(threshold_sec=15 * 60)
        n2 = reap_once(threshold_sec=15 * 60)
        assert n1 == 0
        assert n2 == 0

        # Cause must remain whatever it was (i.e. NOT 'Stale-Session-Timeout')
        row = db().execute(
            "SELECT acctterminatecause FROM radacct WHERE username='finished'"
        ).fetchone()
        assert row["acctterminatecause"] != "Stale-Session-Timeout"


def test_reaper_handles_multiple_rows_atomically(app):
    """A real-world case: multiple zombies across users, only the stale
    ones should close — and in one query."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.workers.stale_session_reaper import reap_once

        with transaction() as c:
            _seed(c, username="ahmad-old1",  started_min_ago=120, updated_min_ago=60)
            _seed(c, username="ahmad-old2",  started_min_ago=120, updated_min_ago=None)
            _seed(c, username="ali-alive",   started_min_ago=10,  updated_min_ago=2)
            _seed(c, username="omar-closed", started_min_ago=120, updated_min_ago=60,
                   stopped=True)

        n = reap_once(threshold_sec=15 * 60)
        assert n == 2  # only the two stale open rows

        open_left = db().execute(
            "SELECT count(*) AS n FROM radacct WHERE acctstoptime IS NULL"
        ).fetchone()["n"]
        assert open_left == 1  # ali still alive
