"""QA: spurious Accounting-On must not wipe just-started sessions.

A flapping accounting link makes a MikroTik re-emit Accounting-On seconds after
a genuine Acct-Start, which previously closed the live session (NAS-Reboot) so it
vanished from /online and CoA couldn't target it. The guard preserves sessions
that started/updated within a debounce window while still closing stale ones and
honouring real per-session Acct-Stop.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def app(monkeypatch, tmp_path):
    dbf = os.path.join(tmp_path, "acct_onoff.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", dbf)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(dbf)
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return a


def _svc():
    from app.radius.services.accounting_events import AccountingEventsService
    return AccountingEventsService()


def _db():
    from app.radius.db.connection import db
    return db()


def _open_row(sid, nas, started):
    _db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctupdatetime, callingstationid, "
        "framedipaddress, acctinputoctets, acctoutputoctets, acctsessiontime) "
        "VALUES(1,?,?,?,?,?,?,?,?,0,0,0)",
        (sid, sid + "-u", "ahmad", nas, started, started, "AA:BB:CC:DD:EE:FF", "10.0.0.5"),
    )


def _stoptime(sid):
    return _db().execute(
        "SELECT acctstoptime FROM radacct WHERE acctsessionid=?", (sid,)
    ).fetchone()["acctstoptime"]


def test_phantom_accounting_on_preserves_fresh_session(app):
    with app.app_context():
        nas = "10.9.9.9"
        now = datetime.utcnow().isoformat() + "Z"
        old = (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
        _open_row("S-FRESH", nas, now)
        _open_row("S-OLD", nas, old)
        res = _svc().ingest(tenant_id=1, payload={
            "status_type": "Accounting-On", "nas_ip_address": nas})
        assert res["preserved"] == 1
        assert res["closed"] == 1
        assert _stoptime("S-FRESH") is None       # just-started -> preserved
        assert _stoptime("S-OLD") is not None      # stale -> still closed


def test_real_acct_stop_still_closes(app):
    with app.app_context():
        nas = "10.9.9.8"
        _svc().ingest(tenant_id=1, payload={
            "status_type": "Start", "acct_session_id": "S1", "nas_ip_address": nas,
            "username": "ahmad", "framed_ip_address": "10.0.0.5",
            "calling_station_id": "AA:BB:CC:DD:EE:FF"})
        assert _stoptime("S1") is None
        _svc().ingest(tenant_id=1, payload={
            "status_type": "Stop", "acct_session_id": "S1", "nas_ip_address": nas,
            "username": "ahmad"})
        assert _stoptime("S1") is not None         # real teardown still works


def test_debounce_zero_restores_close_all(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ACCT_ONOFF_DEBOUNCE_SEC", "0")
    with app.app_context():
        nas = "10.9.9.7"
        now = datetime.utcnow().isoformat() + "Z"
        _open_row("S-FRESH2", nas, now)
        res = _svc().ingest(tenant_id=1, payload={
            "status_type": "Accounting-On", "nas_ip_address": nas})
        assert res["closed"] == 1 and res["preserved"] == 0
        assert _stoptime("S-FRESH2") is not None    # guard off -> closed
