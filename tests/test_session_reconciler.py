"""Session reconciler — close orphan radacct sessions via the canonical
Accounting-Stop path so the «المتصلون الآن» counter stops over-counting
phantom sessions.

The live bug: MikroTik (and most NAS) don't always send Acct-Stop (device
vanishes, NAS reboot without Accounting-On, lost UDP). Rows stay open with
0/0 bytes forever and inflate the dashboard counter / /admin/radius/online
while nobody is actually online.

Coverage:
  1. close_session_row computes acctsessiontime (start→stop) + sets cause,
     using last-known activity (not now) as acctstoptime.
  2. close_session_row is idempotent (already-closed row untouched).
  3. reconcile_stale_interim closes only sessions past the threshold.
  4. A fresh session (recent interim) is NEVER closed.
  5. reconcile_now with NO reachable routers falls back to interim-only —
     it does NOT nuke a fresh session just because the live set is empty.
  6. An UNREACHABLE configured router (live fetch → None) does not cause a
     fresh session on it to be closed (timeout rule only).
  7. After reconcile, the open-row count reflects only the live session(s).
  8. HOBERADIUS_SESSION_STALE_MINUTES tunes the threshold (back-compat sec).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_reconcile_")
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
    # Production format: ISO «…Thh:mm:ssZ» (accounting_events._utcnow). The
    # reconciler must close THIS format — the original reaper's space-form
    # datetime('now') comparison silently never matched it.
    return dt.isoformat() + "Z"


def _seed(conn, *, username, started_min_ago, updated_min_ago=None,
          stopped=False, nas="10.10.0.2", session_id=None,
          bytes_in=0, bytes_out=0, tenant_id=1):
    start = datetime.utcnow() - timedelta(minutes=started_min_ago)
    upd = (datetime.utcnow() - timedelta(minutes=updated_min_ago)
           if updated_min_ago is not None else None)
    stop = datetime.utcnow() if stopped else None
    sid = session_id or f"s-{username}-{started_min_ago}"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
             callingstationid, acctstarttime, acctupdatetime, acctstoptime,
             acctinputoctets, acctoutputoctets)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (tenant_id, sid, f"u-{sid}", username, nas, "AA:BB:CC:DD:EE:01",
          _iso(start), _iso(upd) if upd else None,
          _iso(stop) if stop else None, bytes_in, bytes_out))


def _open_count(db, tenant_id=1):
    return db.execute(
        "SELECT COUNT(*) AS c FROM radacct "
        "WHERE tenant_id=? AND acctstoptime IS NULL", (tenant_id,)
    ).fetchone()["c"]


# ── 1. canonical close computes acctsessiontime + cause ────────────────────
def test_close_row_computes_session_time_and_cause(app):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import session_reconciler as sr

        with transaction() as c:
            _seed(c, username="ghost", started_min_ago=60, updated_min_ago=20)

        row = db().execute(
            "SELECT radacctid, acctstarttime, acctupdatetime, acctsessiontime "
            "FROM radacct WHERE username='ghost'").fetchone()
        with transaction() as c:
            n = sr.close_session_row(c, row, cause=sr.CAUSE_INTERIM)
        assert n == 1

        out = db().execute(
            "SELECT acctstoptime, acctupdatetime, acctsessiontime, "
            "acctterminatecause FROM radacct WHERE username='ghost'").fetchone()
        # stoptime = last sign of life (acctupdatetime), NOT now
        assert out["acctstoptime"] == out["acctupdatetime"]
        assert out["acctterminatecause"] == "Stale-Session-Timeout"
        # start 60m ago, stop 20m ago → 40 min = 2400s (allow ±2s rounding)
        assert abs(int(out["acctsessiontime"]) - 2400) <= 2


# ── 2. idempotent — already-closed row untouched ───────────────────────────
def test_close_row_idempotent(app):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import session_reconciler as sr

        with transaction() as c:
            _seed(c, username="done", started_min_ago=60, updated_min_ago=30,
                  stopped=True)
        row = db().execute(
            "SELECT radacctid, acctstarttime, acctupdatetime, acctsessiontime "
            "FROM radacct WHERE username='done'").fetchone()
        with transaction() as c:
            n = sr.close_session_row(c, row, cause=sr.CAUSE_MANUAL)
        assert n == 0  # already closed → no-op
        cause = db().execute(
            "SELECT acctterminatecause FROM radacct WHERE username='done'"
        ).fetchone()["acctterminatecause"]
        assert cause != "Reconciliation-Stale"


# ── 3 + 4. interim pass closes stale, leaves fresh ─────────────────────────
def test_interim_closes_stale_keeps_fresh(app):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import session_reconciler as sr

        with transaction() as c:
            _seed(c, username="zombie", started_min_ago=120, updated_min_ago=40)
            _seed(c, username="alive", started_min_ago=10, updated_min_ago=2)
            _seed(c, username="never-interim", started_min_ago=90,
                  updated_min_ago=None)

        # threshold 20 min: zombie (40m) + never-interim (start 90m) close,
        # alive (2m) stays.
        closed = sr.reconcile_stale_interim(tenant_id=1, threshold_sec=20 * 60)
        assert closed == 2
        assert _open_count(db()) == 1
        alive = db().execute(
            "SELECT acctstoptime FROM radacct WHERE username='alive'"
        ).fetchone()
        assert alive["acctstoptime"] is None


# ── 5. reconcile_now with no routers → interim-only, doesn't nuke fresh ─────
def test_reconcile_now_no_routers_is_interim_only(app):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import session_reconciler as sr

        with transaction() as c:
            _seed(c, username="zombie", started_min_ago=120, updated_min_ago=40)
            _seed(c, username="alive", started_min_ago=10, updated_min_ago=2)

        # No mikrotik_configs / nas_devices seeded → live pass closes nothing.
        stats = sr.reconcile_now(1, threshold_sec=20 * 60)
        assert stats["live_closed"] == 0
        assert stats["interim_closed"] == 1
        assert stats["closed_total"] == 1
        # fresh session survives — counter now matches the single live session
        assert _open_count(db()) == 1


# ── 6. unreachable router → fresh session on it is NOT closed ──────────────
def test_unreachable_router_does_not_close_fresh(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import session_reconciler as sr
        from app.workers import mt_reconciler

        with transaction() as c:
            _seed(c, username="alive", started_min_ago=10, updated_min_ago=2,
                  nas="10.10.0.9")

        # Pretend a router exists but is unreachable (live fetch → None).
        monkeypatch.setattr(mt_reconciler, "_collect_router_configs",
                            lambda tid: [{"host": "10.10.0.9", "id": 1}])
        monkeypatch.setattr(mt_reconciler, "_fetch_active_rows",
                            lambda cfg: None)

        stats = sr.reconcile_now(1, threshold_sec=20 * 60)
        # router skipped (unreachable), fresh interim → nothing closed
        assert stats["routers_skipped"] == 1
        assert stats["closed_total"] == 0
        assert _open_count(db()) == 1


# ── 7. count reflects only live after reconcile ────────────────────────────
def test_counter_honest_after_reconcile(app):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import live_sessions, session_reconciler as sr

        with transaction() as c:
            # one genuinely live session (fresh interim) + two orphans
            _seed(c, username="real", started_min_ago=5, updated_min_ago=1)
            _seed(c, username="ghost1", started_min_ago=120, updated_min_ago=60)
            _seed(c, username="ghost2", started_min_ago=200, updated_min_ago=None)

        # Before: raw open-row count is inflated (3) vs windowed live (1)
        assert _open_count(db()) == 3
        assert live_sessions.tenant_active_count(1) == 1

        sr.reconcile_now(1, threshold_sec=20 * 60)

        # After: raw open count agrees with the windowed live count
        assert _open_count(db()) == 1
        assert live_sessions.tenant_active_count(1) == 1


# ── 8. HOBERADIUS_SESSION_STALE_MINUTES tunes the threshold ────────────────
def test_threshold_env_minutes_and_legacy_sec(app, monkeypatch):
    with app.app_context():
        from app.radius.services import session_reconciler as sr

        monkeypatch.delenv("HOBERADIUS_SESSION_STALE_MINUTES", raising=False)
        monkeypatch.delenv("HOBERADIUS_STALE_SESSION_SEC", raising=False)
        assert sr.stale_threshold_sec() == 20 * 60  # default 20 min

        monkeypatch.setenv("HOBERADIUS_SESSION_STALE_MINUTES", "30")
        assert sr.stale_threshold_sec() == 30 * 60

        # new minutes var wins over legacy sec var
        monkeypatch.setenv("HOBERADIUS_STALE_SESSION_SEC", "120")
        assert sr.stale_threshold_sec() == 30 * 60

        # legacy sec var used when minutes var is absent
        monkeypatch.delenv("HOBERADIUS_SESSION_STALE_MINUTES", raising=False)
        assert sr.stale_threshold_sec() == 120
