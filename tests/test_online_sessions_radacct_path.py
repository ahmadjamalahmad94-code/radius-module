"""R8.2 regression: OnlineSessionsService.list() must read live sessions
from radacct, not from a synchronous MikroTik API call.

The bug it guards against: previously `OnlineSessionsService.list()`
delegated to `SqliteAdapter.list_online()`, which iterates each tenant's
MT routers and synchronously calls `/ip/hotspot/active/print`. If a
router is unreachable, the call blocks for ~45s per router, making
`/admin/radius/online` hang 1–3 minutes / return 504. After R8.2 the
sessions list reads radacct directly — microseconds, no network.

Coverage:
  1. list() returns OnlineSession DTOs derived from radacct open rows.
  2. Closed sessions (acctstoptime IS NOT NULL) are excluded.
  3. list() never calls adapter.list_online() — the legacy MT path.
  4. Field mapping matches the contract (username, session_id, NAS,
     framed IP, MAC, bytes, timestamps, tenant_id).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    """Fresh app on a temp DB. monkeypatch keeps env isolated."""
    tmp = tempfile.mkdtemp(prefix="hr_r82_")
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


def _insert_radacct(conn, *, tenant_id=1, session_id, username, nas_ip,
                     framed_ip="", mac="",
                     bytes_in=0, bytes_out=0, closed=False,
                     nasporttype=""):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
             nasporttype, framedipaddress, callingstationid,
             acctstarttime, acctupdatetime, acctstoptime,
             acctinputoctets, acctoutputoctets)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (tenant_id, session_id, f"{nas_ip}-{session_id}-{username}",
          username, nas_ip, nasporttype, framed_ip, mac,
          now, now,
          now if closed else None,
          bytes_in, bytes_out))


def test_list_returns_open_sessions_from_radacct(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.sessions import get_online_sessions_service

        with transaction() as c:
            _insert_radacct(c, session_id="s-open-1", username="ali",
                              nas_ip="10.0.0.1", framed_ip="192.168.1.5",
                              mac="AA:BB:CC:DD:EE:01",
                              bytes_in=1234, bytes_out=5678,
                              nasporttype="Ethernet")
            _insert_radacct(c, session_id="s-open-2", username="ahmad",
                              nas_ip="10.0.0.1", framed_ip="192.168.1.6",
                              mac="AA:BB:CC:DD:EE:02",
                              bytes_in=100, bytes_out=200)
            _insert_radacct(c, session_id="s-closed", username="someone",
                              nas_ip="10.0.0.1", bytes_in=99, bytes_out=99,
                              closed=True)

        out = list(get_online_sessions_service().list(limit=50))

        usernames = sorted(s.username for s in out)
        assert usernames == ["ahmad", "ali"], \
            f"closed session leaked OR open sessions missing: {usernames}"

        # Field mapping for s-open-1
        s = next(x for x in out if x.username == "ali")
        assert s.session_id == "s-open-1"
        assert s.nas_address == "10.0.0.1"
        assert s.nas_id == "10.0.0.1"  # we use IP for both today
        assert s.framed_ip == "192.168.1.5"
        assert s.mac_address == "AA:BB:CC:DD:EE:01"
        assert s.bytes_in == 1234
        assert s.bytes_out == 5678
        assert s.nas_port_type == "Ethernet"
        assert s.tenant_id == 1


def test_list_does_not_call_legacy_list_online(app, monkeypatch):
    """Smoking-gun: even if list_online() would explode, list() must
    succeed. We replace the legacy MT-API method with a raise and verify
    it's never invoked from the render path."""
    with app.app_context():
        from app.radius.services.sessions import get_online_sessions_service
        svc = get_online_sessions_service()

        def _boom(*args, **kwargs):
            raise RuntimeError("legacy list_online must not be called from list()")

        monkeypatch.setattr(svc._adapter, "list_online", _boom)
        out = list(svc.list(limit=10))  # must not raise
        assert out == []  # empty DB → empty list


def test_list_limit_is_respected(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.sessions import get_online_sessions_service

        with transaction() as c:
            for i in range(7):
                _insert_radacct(c, session_id=f"s{i}", username=f"u{i}",
                                  nas_ip="10.0.0.1")

        out = list(get_online_sessions_service().list(limit=3))
        assert len(out) == 3
