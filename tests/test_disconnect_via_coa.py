"""R11.16 regression: the "قطع" button must route through CoA Disconnect
(UDP/3799) rather than the MT API sync queue (TCP/8728).

Pre-R11.16 the button called `enqueue_disconnect` → sync_worker → MT API,
which silently failed in deployments behind NAT or without a public IP
(the queue would just keep retrying forever). Post-R11.16 the adapter
calls `disconnect_user` from radius_coa, which travels over the same UDP
channel that already works for accounting + CoA rate changes.

Coverage:
 1. Adapter.disconnect routes through radius_coa.disconnect_user (no
    sync_queue enqueue).
 2. Successful CoA-ACK is a no-op (no RadiusError raised).
 3. CoA failure (no_active_session, CoA-NAK, timeout) raises RadiusError
    so the route's flash shows a meaningful message instead of silently
    succeeding.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r1116_")
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


def _adapter():
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    return SqliteAdapter()


def test_adapter_disconnect_calls_radius_coa_not_sync_queue(app, monkeypatch):
    """Adapter.disconnect must reach radius_coa.disconnect_user. The old
    path (enqueue_disconnect) must NOT be invoked."""
    with app.app_context():
        from app.radius.integration import radius_coa, router_sync

        coa_calls = []
        def _fake_coa(tenant_id, username):
            coa_calls.append((tenant_id, username))
            return radius_coa.CoaResult(ok=True, code=41,
                                         code_name="Disconnect-ACK",
                                         reply_message="acked")
        monkeypatch.setattr(radius_coa, "disconnect_user", _fake_coa)

        enq_calls = []
        def _fake_enqueue(tenant_id, username):
            enq_calls.append((tenant_id, username))
        monkeypatch.setattr(router_sync, "enqueue_disconnect", _fake_enqueue)

        _adapter().disconnect("ahmad")

        assert coa_calls == [(1, "ahmad")], \
            "disconnect must call radius_coa.disconnect_user"
        assert enq_calls == [], \
            "disconnect must NOT enqueue to MT API sync_queue (R11.16)"


def test_adapter_disconnect_silent_on_success(app, monkeypatch):
    """A CoA-ACK result must complete without raising."""
    with app.app_context():
        from app.radius.integration import radius_coa

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=True, code=41, code_name="Disconnect-ACK",
                reply_message=""))

        _adapter().disconnect("ahmad")  # no exception


def test_adapter_disconnect_raises_on_failure(app, monkeypatch):
    """No active session → RadiusError propagates so the flash message
    shows the user what happened."""
    with app.app_context():
        from app.radius.core.errors import RadiusError
        from app.radius.integration import radius_coa

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=False, code=0, code_name="no_active_session",
                reply_message="لا جلسة نشطة لـ ahmad"))

        with pytest.raises(RadiusError) as ei:
            _adapter().disconnect("ahmad")
        assert "ahmad" in str(ei.value) or "لا جلسة" in str(ei.value)


def test_adapter_disconnect_raises_on_coa_nak(app, monkeypatch):
    """A CoA-NAK (e.g. NAS rejected) must surface as RadiusError too."""
    with app.app_context():
        from app.radius.core.errors import RadiusError
        from app.radius.integration import radius_coa

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=False, code=42, code_name="Disconnect-NAK",
                reply_message="Session not found"))

        with pytest.raises(RadiusError):
            _adapter().disconnect("ghost")


# ─────────── R11.18: close zombie radacct rows after ACK ───────────

def _seed_open_session(conn, *, username, nas_ip, session_id):
    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, framedipaddress, callingstationid, acctstarttime)
        VALUES (?,?,?,?,?,?,?,?)
    """, (1, session_id, f"u-{session_id}", username, nas_ip,
           "10.20.30.254", "AA:BB:CC:DD:EE:FF", now))


def test_disconnect_closes_all_open_radacct_rows_for_username(app, monkeypatch):
    """R11.18: after Disconnect-ACK, EVERY radacct row with acctstoptime
    IS NULL for that username must be closed — the zombie rows that drove
    R11.18 (multiple stale "active" sessions for ahmad in the UI) come
    from MT not sending Acct-Stop after CoA-initiated disconnects."""
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.integration import radius_coa

        with transaction() as c:
            _seed_open_session(c, username="ahmad", nas_ip="213.6.169.138",
                               session_id="old-public-ip")
            _seed_open_session(c, username="ahmad", nas_ip="192.168.1.186",
                               session_id="old-lan-ip")
            _seed_open_session(c, username="ahmad", nas_ip="10.10.0.2",
                               session_id="current-wg")
            # A different user's session must NOT be touched
            _seed_open_session(c, username="other", nas_ip="10.10.0.2",
                               session_id="bystander")

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=True, code=41, code_name="Disconnect-ACK",
                reply_message="acked"))

        _adapter().disconnect("ahmad")

        # All ahmad rows closed
        open_for_ahmad = db().execute(
            "SELECT count(*) AS n FROM radacct "
            "WHERE username='ahmad' AND acctstoptime IS NULL"
        ).fetchone()
        assert open_for_ahmad["n"] == 0

        # Other user untouched
        open_for_other = db().execute(
            "SELECT count(*) AS n FROM radacct "
            "WHERE username='other' AND acctstoptime IS NULL"
        ).fetchone()
        assert open_for_other["n"] == 1

        # Closed rows carry the right terminate cause
        causes = [r["acctterminatecause"] for r in db().execute(
            "SELECT acctterminatecause FROM radacct "
            "WHERE username='ahmad'").fetchall()]
        assert causes == ["Admin-Reset", "Admin-Reset", "Admin-Reset"]


def test_disconnect_does_not_close_radacct_on_failure(app, monkeypatch):
    """If CoA-NAK / no-active-session / timeout, the row stays open —
    we only close on a confirmed ACK from the NAS."""
    with app.app_context():
        from app.radius.core.errors import RadiusError
        from app.radius.db.connection import db, transaction
        from app.radius.integration import radius_coa

        with transaction() as c:
            _seed_open_session(c, username="ahmad", nas_ip="10.10.0.2",
                               session_id="live")

        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=False, code=0, code_name="timeout",
                reply_message="NAS لم يستجب"))

        with pytest.raises(RadiusError):
            _adapter().disconnect("ahmad")

        # Row still open — we don't lie about disconnect on failure
        n = db().execute(
            "SELECT count(*) AS n FROM radacct "
            "WHERE username='ahmad' AND acctstoptime IS NULL"
        ).fetchone()["n"]
        assert n == 1
