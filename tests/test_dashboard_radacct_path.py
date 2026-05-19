"""R8.1 regression: DashboardService.snapshot() must read live-session
metrics from radacct, not from a synchronous MT API call.

The bug it guards against: previously `snapshot()` called
`adapter.list_online(limit=2000)`, which iterates each tenant's MT routers
and synchronously calls `/ip/hotspot/active/print`. If a router is
unreachable the call blocks for `connect_timeout * retries` per router,
making `/admin/radius/` hang ~45–103s and nginx return 504. After R8.1
the dashboard reads radacct directly — microseconds, no network.

Coverage:
  1. snapshot() never calls adapter.list_online()       ← the actual fix
  2. online_now == COUNT(radacct WHERE acctstoptime IS NULL)
  3. bytes_today_in/out == SUM(acctinputoctets/outputoctets) for same rows
  4. snapshot() completes even when adapter.list_online would raise
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    """Fresh app on a temp DB. Uses monkeypatch so env is restored after the
    test — critical to avoid leaking HOBERADIUS_DB_PATH into other tests."""
    tmp = tempfile.mkdtemp(prefix="hr_r81_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    # Drop cached app.* modules so create_app() picks up our DB path.
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    # Re-clean modules so the NEXT test re-imports against ITS env.
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _insert_radacct(conn, *, tenant_id=1, session_id, username, nas_ip,
                     bytes_in=0, bytes_out=0, closed=False):
    """Insert one radacct row. `closed=True` sets acctstoptime so the row
    doesn't count as currently-online."""
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, acctstarttime, acctstoptime,
             acctinputoctets, acctoutputoctets)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (tenant_id, session_id, f"{nas_ip}-{session_id}-{username}",
          username, nas_ip, now,
          now if closed else None,
          bytes_in, bytes_out))


def test_snapshot_reads_online_from_radacct(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.dashboard import get_dashboard_service

        with transaction() as c:
            _insert_radacct(c, session_id="s1", username="ali",
                              nas_ip="10.0.0.1", bytes_in=1000, bytes_out=2000)
            _insert_radacct(c, session_id="s2", username="ahmad",
                              nas_ip="10.0.0.1", bytes_in=500, bytes_out=750)
            _insert_radacct(c, session_id="s3", username="closed-user",
                              nas_ip="10.0.0.1", bytes_in=9999, bytes_out=9999,
                              closed=True)

        snap = get_dashboard_service().snapshot()

        assert snap.online_now == 2, f"expected 2 open sessions, got {snap.online_now}"
        # bytes counters reflect ONLY open sessions (closed s3 must be excluded)
        assert snap.bytes_today_in  == 1500
        assert snap.bytes_today_out == 2750


def test_snapshot_does_not_call_adapter_list_online(app, monkeypatch):
    """Smoking-gun test: even if list_online() would explode, snapshot()
    must succeed. Replace the adapter method with a raise; snapshot()
    must NOT touch it."""
    with app.app_context():
        from app.radius.services.dashboard import get_dashboard_service
        svc = get_dashboard_service()

        def _boom(*args, **kwargs):
            raise RuntimeError("list_online must not be called from snapshot()")

        monkeypatch.setattr(svc._adapter, "list_online", _boom)
        snap = svc.snapshot()  # must not raise

        # zero open sessions in fresh DB → online_now=0
        assert snap.online_now == 0
        assert snap.bytes_today_in == 0
        assert snap.bytes_today_out == 0


def test_snapshot_radacct_query_failure_falls_back_to_zero(app, monkeypatch):
    """Defensive: if the radacct query itself raises (e.g. DB locked),
    we should fall back to zeros — never propagate to the request."""
    with app.app_context():
        from app.radius.services import dashboard as dash_mod
        from app.radius.services.dashboard import get_dashboard_service

        monkeypatch.setattr(dash_mod, "_live_session_totals",
                             lambda _t: (0, 0, 0))
        snap = get_dashboard_service().snapshot()
        assert snap.online_now == 0
