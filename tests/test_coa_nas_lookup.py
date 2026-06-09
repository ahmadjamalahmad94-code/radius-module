"""R9.1 regression: CoA Disconnect must find the NAS secret in nas_devices.

The previous code queried the FreeRADIUS `nas` table — populated only when
`read_clients = yes` in mods-enabled/sql. Our setup uses `read_clients = no`
so that table is empty. Result: every "disconnect" button click failed with
`missing_nas_secret`.

The fix reads from `nas_devices` instead (the table the UI populates).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r91_")
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


def _seed_active_session(conn, *, tenant_id=1, username, nas_ip,
                          session_id="s-test-1"):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, acctstarttime)
        VALUES (?,?,?,?,?,?)
    """, (tenant_id, session_id, f"u-{session_id}", username, nas_ip, now))


def _seed_nas_device(conn, *, tenant_id=1, name, address, secret, enabled=1):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO nas_devices
            (tenant_id, name, address, secret, vendor, nas_type, enabled, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (tenant_id, name, address, secret, "mikrotik", "hotspot", enabled, now))


def test_find_nas_returns_secret_from_nas_devices(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration.radius_coa import find_nas_for_session

        with transaction() as c:
            _seed_nas_device(c, name="mt-main", address="192.168.1.186",
                              secret="mySecret123")
            _seed_active_session(c, username="ahmad", nas_ip="192.168.1.186",
                                  session_id="s-active")

        info = find_nas_for_session(1, "ahmad")
        assert info is not None
        assert info["nas_ip"] == "192.168.1.186"
        assert info["nas_secret"] == "mySecret123"
        assert info["session_id"] == "s-active"


def test_find_nas_returns_none_when_no_active_session(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration.radius_coa import find_nas_for_session

        with transaction() as c:
            _seed_nas_device(c, name="mt-main", address="192.168.1.186",
                              secret="x")
            # no radacct insert — no active session
        assert find_nas_for_session(1, "ghost") is None


def test_find_nas_skips_disabled_devices(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration.radius_coa import find_nas_for_session

        with transaction() as c:
            _seed_nas_device(c, name="mt-disabled", address="10.0.0.99",
                              secret="should-not-leak", enabled=0)
            _seed_active_session(c, username="ali", nas_ip="10.0.0.99")

        info = find_nas_for_session(1, "ali")
        # session found but secret empty because device disabled
        assert info is not None
        assert info["nas_secret"] == ""


def test_disconnect_user_reaches_send_disconnect(app, monkeypatch):
    """End-to-end: disconnect_user() resolves session+secret and calls
    send_disconnect with the correct args. We mock send_disconnect to
    avoid actual UDP traffic."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa

        with transaction() as c:
            _seed_nas_device(c, name="mt-main", address="192.168.1.186",
                              secret="kick-secret")
            _seed_active_session(c, username="omar", nas_ip="192.168.1.186",
                                  session_id="sess-omar")

        calls = {}
        # R11.12: send_disconnect now also accepts framed_ip + calling_station_id
        # (defaults to empty when the radacct row hasn't been populated by an
        # R10.6-era Acct-Start). The mock must accept them or fail with
        # TypeError.
        def _fake_send_disconnect(*, nas_ip, nas_secret, username, session_id,
                                    framed_ip="", calling_station_id="",
                                    port=3799):
            calls.update(dict(nas_ip=nas_ip, nas_secret=nas_secret,
                              username=username, session_id=session_id,
                              framed_ip=framed_ip,
                              calling_station_id=calling_station_id,
                              port=port))
            return radius_coa.CoaResult(
                ok=True, code=41, code_name="Disconnect-ACK",
                reply_message="acked")
        monkeypatch.setattr(radius_coa, "send_disconnect", _fake_send_disconnect)

        result = radius_coa.disconnect_user(1, "omar")

        assert result.ok is True
        assert calls == {
            "nas_ip": "192.168.1.186",
            "nas_secret": "kick-secret",
            "username": "omar",
            "session_id": "sess-omar",
            # session row was seeded without framedipaddress/callingstationid,
            # so both come back as empty strings (see find_nas_for_session).
            "framed_ip": "",
            "calling_station_id": "",
            "port": 3799,
        }
