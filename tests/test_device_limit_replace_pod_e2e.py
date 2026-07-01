"""End-to-end proof that max=1 + replace ("newest-login-wins") genuinely
kicks the existing device at AUTHORIZE time:

  1. The live radacct session count is read from real open rows (production
     FreeRADIUS "space" timestamp format included), so the limit trips.
  2. A real CoA/PoD Disconnect-Request is dispatched to the NAS for the
     OLDEST session (send_disconnect is reached with the right NAS ip/secret
     + session id) — we mock the UDP send to capture the call, not skip it.
  3. The old radacct row is closed (acctstoptime written via the canonical
     Accounting-Stop path) so the count frees and the new login is admitted.

This closes the owner-reported gap ("1-device user still gets 2 sessions"):
the existing device_limit tests seed no nas_devices, so disconnect_user
no-ops and never exercises the packet path. Here we seed the NAS.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_devlimit_pod_")
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


NAS_IP = "10.50.0.7"
NAS_SECRET = "kick-secret-xyz"
MAC_OLD = "AA:BB:CC:00:00:01"
MAC_NEW = "AA:BB:CC:00:00:02"


def _seed_nas(conn):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute(
        "INSERT INTO nas_devices "
        "(tenant_id, name, address, secret, vendor, nas_type, enabled, created_at) "
        "VALUES (1,?,?,?,?,?,1,?)",
        ("mt-main", NAS_IP, NAS_SECRET, "mikrotik", "hotspot", now))


def _seed_open_session(conn, *, sid, mac, age_min, fmt="freeradius"):
    when = datetime.utcnow() - timedelta(minutes=age_min)
    ts = (when.strftime("%Y-%m-%d %H:%M:%S") if fmt == "freeradius"
          else when.isoformat() + "Z")
    conn.execute(
        "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
        " nasipaddress, callingstationid, acctstarttime, acctupdatetime) "
        "VALUES (1,?,?,?,?,?,?,?)",
        (sid, f"u-{sid}", "sub1", NAS_IP, mac, ts, ts))


def _mk_sub(**kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username="sub1", password="pw", tenant_id=1,
                status="enabled")
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


def test_replace_fires_pod_to_nas_and_admits_new(app, monkeypatch):
    """max=1 + replace: a new login from a different device fires a real
    Disconnect-Request to the NAS for the oldest session, closes its radacct
    row, and admits the new login."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        from app.radius.services.policy_engine import AuthRequest, authorize

        with transaction() as c:
            _seed_nas(c)
            # existing device, production "space" timestamp, live now
            _seed_open_session(c, sid="old-sess", mac=MAC_OLD, age_min=1)
        _mk_sub(device_count=1, device_limit_mode="replace")

        # Capture the PoD instead of sending real UDP.
        calls = []

        def _fake_send_disconnect(*, nas_ip, nas_secret, username, session_id,
                                  framed_ip="", calling_station_id="", port=3799):
            calls.append(dict(nas_ip=nas_ip, nas_secret=nas_secret,
                              username=username, session_id=session_id))
            return radius_coa.CoaResult(ok=True, code=41,
                                        code_name="Disconnect-ACK",
                                        reply_message="acked")
        monkeypatch.setattr(radius_coa, "send_disconnect", _fake_send_disconnect)

        # New login from a DIFFERENT device.
        d = authorize(AuthRequest(username="sub1", password="pw", tenant_id=1,
                                  calling_station_id=MAC_NEW))

        # (a) new login admitted (newest-wins)
        assert d.ok is True, f"new login must be admitted, got reason={d.reason}"

        # (b) a real PoD fired to the NAS for the OLDEST session
        assert len(calls) == 1, f"expected exactly one Disconnect-Request, got {calls}"
        assert calls[0]["nas_ip"] == NAS_IP
        assert calls[0]["nas_secret"] == NAS_SECRET
        assert calls[0]["session_id"] == "old-sess"
        assert calls[0]["username"] == "sub1"

        # (c) the old radacct row is closed (freed from the live count)
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT acctstoptime, acctterminatecause FROM radacct "
            "WHERE tenant_id=1 AND acctsessionid='old-sess'").fetchone()
        assert row["acctstoptime"] not in (None, ""), "old session must be closed"
        assert row["acctterminatecause"] == "Device-Limit-Replace"


def test_replace_admits_new_even_if_pod_undeliverable(app, monkeypatch):
    """If the NAS is unreachable (Disconnect times out / no ACK) the new
    login is STILL admitted and the stale row is force-closed — a transient
    unreachable router must never permanently lock the user out."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        from app.radius.services.policy_engine import AuthRequest, authorize

        with transaction() as c:
            _seed_nas(c)
            _seed_open_session(c, sid="old-sess", mac=MAC_OLD, age_min=1)
        _mk_sub(device_count=1, device_limit_mode="replace")

        def _timeout_send_disconnect(**kw):
            return radius_coa.CoaResult(ok=False, code=0, code_name="timeout",
                                        reply_message="no ACK")
        monkeypatch.setattr(radius_coa, "send_disconnect", _timeout_send_disconnect)

        d = authorize(AuthRequest(username="sub1", password="pw", tenant_id=1,
                                  calling_station_id=MAC_NEW))
        assert d.ok is True, "unreachable NAS must not lock the user out"
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT acctstoptime FROM radacct WHERE acctsessionid='old-sess'"
        ).fetchone()
        assert row["acctstoptime"] not in (None, ""), \
            "old row force-closed so the new session fits the count"


def test_reject_mode_blocks_second_device_freeradius_ts(app):
    """Sanity: same seed under reject mode → Access-Reject (the exact
    owner-reported '1-device still gets 2 sessions' scenario, now blocked)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.policy_engine import AuthRequest, authorize

        with transaction() as c:
            _seed_nas(c)
            _seed_open_session(c, sid="old-sess", mac=MAC_OLD, age_min=1)
        _mk_sub(device_count=1, device_limit_mode="reject")

        d = authorize(AuthRequest(username="sub1", password="pw", tenant_id=1,
                                  calling_station_id=MAC_NEW))
        assert d.ok is False
        assert d.reason == "concurrent_limit"
        assert "بلغت الحد الأقصى" in (d.reply_attrs.get("Reply-Message") or "")
