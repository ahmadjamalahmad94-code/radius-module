"""MikroTikActiveSessionReconciler + reconcile-first disconnect.

Root cause guarded here: `radius_coa` used to build Disconnect-Request session
keys (Acct-Session-Id / Framed-IP / Calling-Station-Id) from `radacct`, which
goes stale after tunnel loss / router reboot / FreeRADIUS restart. MikroTik then
NAKs with "Radius disconnect request has wrong attributes". Now we reconcile
against the router's real `/ip/hotspot/active` list first and build the packet
from verified-live attributes, refusing (typed error) when we can't identify an
exact active session — and NEVER sending a blind/malformed packet.

Covers the required scenarios:
  • active MikroTik session → canonical online session (create/refresh view)
  • HobeRadius session missing from MikroTik → stale/offline (refuse not_active)
  • API failure does NOT mark offline → freshness-gated radacct fallback
  • disconnect uses reconciled Acct-Session-Id when radacct agrees
  • disconnect refuses when required attributes are missing
  • duplicate-username sessions require exact session/IP/MAC match
  • wrong router/NAS (no secret) is rejected
  • no malformed Disconnect-Request is sent on any refusal
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_activerec_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACTIVE_RECONCILE_TTL_SEC", "0")  # no cache in tests
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# ─────────────── seed helpers ───────────────


def _seed_nas(conn, *, tenant_id=1, name="mt1", address, secret="sekret",
              coa_port=3799, enabled=1):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute(
        "INSERT INTO nas_devices (tenant_id, name, address, secret, coa_port, "
        "vendor, nas_type, enabled, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (tenant_id, name, address, secret, coa_port, "mikrotik", "hotspot",
         enabled, now))


def _seed_radacct(conn, *, tenant_id=1, username, nas_ip, session_id,
                  framed_ip="", mac="", age_min=0.0):
    """Open radacct row with a heartbeat `age_min` minutes in the past."""
    beat = (datetime.utcnow() - timedelta(minutes=age_min)).isoformat() + "Z"
    conn.execute(
        "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, framedipaddress, callingstationid, acctstarttime, "
        "acctupdatetime) VALUES (?,?,?,?,?,?,?,?,?)",
        (tenant_id, session_id, f"u-{session_id}", username, nas_ip,
         framed_ip, mac, beat, beat))


def _hotspot_row(user, address, mac, *, login_by="cookie", uptime="22m",
                 server="hotspot1"):
    return {"user": user, "address": address, "mac-address": mac,
            "login-by": login_by, "uptime": uptime, "idle-time": "0s",
            "server": server, "bytes-in": "100", "bytes-out": "200"}


def _reconciler(tenant_id, *, fetch, host="10.10.0.1", rid=1):
    from app.radius.services.mikrotik_active_reconciler import (
        MikroTikActiveSessionReconciler)
    return MikroTikActiveSessionReconciler(
        tenant_id,
        fetch_active=fetch,
        router_configs=lambda: [{"id": rid, "host": host}],
        now=lambda: 0.0,
    )


# ─────────────── reconciler unit tests ───────────────


def test_active_row_yields_canonical_live_session_with_acct_sid(app):
    """A live hotspot row → canonical LiveSession; Acct-Session-Id is enriched
    from the open radacct row that agrees on MAC (MT active has no acct-sid)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1", secret="s1")
            _seed_radacct(c, username="ali", nas_ip="10.10.0.1",
                          session_id="RAD-77", framed_ip="10.5.0.9",
                          mac="AA:BB:CC:DD:EE:01")
        rows = [_hotspot_row("ali", "10.5.0.9", "AA:BB:CC:DD:EE:01")]
        rec = _reconciler(1, fetch=lambda cfg: rows)
        out = rec.resolve_disconnect_targets("ali")
        assert out.error == "", out.error
        assert len(out.sessions) == 1
        s = out.sessions[0]
        assert s.source == "mikrotik_active"
        assert s.framed_ip_address == "10.5.0.9"
        assert s.calling_station_id == "AA:BB:CC:DD:EE:01"
        assert s.acct_session_id == "RAD-77"        # enriched from radacct
        assert s.nas_secret == "s1"
        assert s.login_by == "cookie" and s.server == "hotspot1"


def test_session_missing_from_router_is_not_active(app):
    """Router reachable but user absent from the live list → session_not_active
    (a stale radacct row must NOT produce a disconnect)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_active_reconciler import ERR_SESSION_NOT_ACTIVE
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1")
            _seed_radacct(c, username="ghost", nas_ip="10.10.0.1",
                          session_id="OLD-1", framed_ip="10.5.0.5",
                          mac="AA:BB:CC:DD:EE:99")
        rec = _reconciler(1, fetch=lambda cfg: [])   # empty active list
        out = rec.resolve_disconnect_targets("ghost")
        assert out.sessions == []
        assert out.error == ERR_SESSION_NOT_ACTIVE


def test_api_failure_reports_unreachable_not_offline(app):
    """fetch returns None (API down) → outcome is router_api_unreachable, NOT
    session_not_active, so the caller can fall back instead of false-closing."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_active_reconciler import ERR_ROUTER_UNREACHABLE
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1")
            _seed_radacct(c, username="ali", nas_ip="10.10.0.1",
                          session_id="RAD-1", framed_ip="10.5.0.9",
                          mac="AA:BB:CC:DD:EE:01")
        rec = _reconciler(1, fetch=lambda cfg: None)
        out = rec.resolve_disconnect_targets("ali")
        assert out.any_router_unreachable
        assert out.error == ERR_ROUTER_UNREACHABLE


def test_duplicate_username_requires_exact_match(app):
    """Two live sessions for one user (different MAC/IP): a selector narrows to
    exactly one; a session_id that isn't live → stale (refuse)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_active_reconciler import ERR_SESSION_STALE
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1")
            _seed_radacct(c, username="dup", nas_ip="10.10.0.1",
                          session_id="S-A", framed_ip="10.5.0.1",
                          mac="AA:AA:AA:AA:AA:A1")
            _seed_radacct(c, username="dup", nas_ip="10.10.0.1",
                          session_id="S-B", framed_ip="10.5.0.2",
                          mac="AA:AA:AA:AA:AA:A2")
        rows = [_hotspot_row("dup", "10.5.0.1", "AA:AA:AA:AA:AA:A1"),
                _hotspot_row("dup", "10.5.0.2", "AA:AA:AA:AA:AA:A2")]
        rec = _reconciler(1, fetch=lambda cfg: rows)

        # both live with no selector
        assert len(rec.resolve_disconnect_targets("dup").sessions) == 2
        # narrow by MAC → exactly one
        one = rec.resolve_disconnect_targets("dup", mac="AA:AA:AA:AA:AA:A2")
        assert len(one.sessions) == 1 and one.sessions[0].framed_ip_address == "10.5.0.2"
        # narrow by acct-session-id → exactly one
        by_sid = rec.resolve_disconnect_targets("dup", session_ids=["S-A"])
        assert len(by_sid.sessions) == 1 and by_sid.sessions[0].acct_session_id == "S-A"
        # a session id that is NOT in the live set → stale refuse
        stale = rec.resolve_disconnect_targets("dup", session_ids=["S-GONE"])
        assert stale.sessions == [] and stale.error == ERR_SESSION_STALE


def test_wrong_nas_without_secret_is_rejected(app):
    """A live session on a host with no enabled nas_devices row → we can't sign
    a packet → cannot_disconnect_missing_session_attributes (never sent)."""
    with app.app_context():
        from app.radius.services.mikrotik_active_reconciler import ERR_MISSING_ATTRS
        # NAS row is on a DIFFERENT host → no secret resolves for 10.10.0.1
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_nas(c, address="10.99.99.99", secret="other")
        rows = [_hotspot_row("ali", "10.5.0.9", "AA:BB:CC:DD:EE:01")]
        rec = _reconciler(1, fetch=lambda cfg: rows, host="10.10.0.1")
        out = rec.resolve_disconnect_targets("ali")
        assert out.sessions == []
        assert out.error == ERR_MISSING_ATTRS


def test_live_session_without_keys_refuses(app):
    """Live row with neither address nor MAC → no strong key → missing attrs."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_active_reconciler import ERR_MISSING_ATTRS
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1")
        rows = [_hotspot_row("ali", "", "")]      # keyless live row
        rec = _reconciler(1, fetch=lambda cfg: rows)
        out = rec.resolve_disconnect_targets("ali")
        assert out.sessions == []
        assert out.error == ERR_MISSING_ATTRS


def test_cookie_session_synthetic_id_targets_live_without_bogus_acct_sid(app):
    """A materialised cookie session has a synthetic `mtsync-…` id. Picking it
    must target the LIVE session by MAC/IP and must NOT send that synthetic id
    as Acct-Session-Id (which would itself provoke a wrong-attributes NAK)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1", secret="s1")
            _seed_radacct(c, username="cook", nas_ip="10.10.0.1",
                          session_id="mtsync-deadbeef", framed_ip="10.5.0.7",
                          mac="AA:BB:CC:DD:EE:07")
        rows = [_hotspot_row("cook", "10.5.0.7", "AA:BB:CC:DD:EE:07")]
        rec = _reconciler(1, fetch=lambda cfg: rows)
        out = rec.resolve_disconnect_targets(
            "cook", session_ids=["mtsync-deadbeef"])
        assert len(out.sessions) == 1
        s = out.sessions[0]
        assert s.framed_ip_address == "10.5.0.7"           # live keys used
        assert s.calling_station_id == "AA:BB:CC:DD:EE:07"
        assert s.acct_session_id == ""                     # synthetic id dropped


# ─────────────── reconcile-first disconnect integration ───────────────


def _patch_defaults(monkeypatch, *, fetch, host="10.10.0.1", rid=1):
    """Point the disconnect path's reconciler at fake router data."""
    import app.radius.services.mikrotik_active_reconciler as mar
    monkeypatch.setattr(mar, "_default_router_configs",
                        lambda tid: [{"id": rid, "host": host}])
    monkeypatch.setattr(mar, "_default_fetch_active", fetch)


def test_disconnect_sends_reconciled_live_attributes(app, monkeypatch):
    """disconnect_user builds the packet from the LIVE session (framed_ip+mac)
    and the reconciled Acct-Session-Id — not from any stale radacct value."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1", secret="s1")
            # radacct row agrees on MAC but has a DIFFERENT (stale) framed_ip
            _seed_radacct(c, username="ali", nas_ip="10.10.0.1",
                          session_id="RAD-77", framed_ip="10.5.0.OLD".replace("OLD", "1"),
                          mac="AA:BB:CC:DD:EE:01")
        live = [_hotspot_row("ali", "10.5.0.42", "AA:BB:CC:DD:EE:01")]
        _patch_defaults(monkeypatch, fetch=lambda cfg: live)

        sent = []
        def _capture(**kw):
            sent.append(kw)
            return radius_coa.CoaResult(ok=True, code=41,
                                        code_name="Disconnect-ACK")
        monkeypatch.setattr(radius_coa, "send_disconnect", _capture)

        res = radius_coa.disconnect_user(1, "ali")
        assert res.ok, res.reply_message
        assert len(sent) == 1
        pkt = sent[0]
        assert pkt["framed_ip"] == "10.5.0.42"          # LIVE ip, not radacct
        assert pkt["calling_station_id"] == "AA:BB:CC:DD:EE:01"
        assert pkt["session_id"] == "RAD-77"            # reconciled acct-sid
        assert pkt["nas_secret"] == "s1"


def test_disconnect_refuses_when_not_active_no_packet(app, monkeypatch):
    """User absent from the reachable router's live list → refuse with typed
    error; NO Disconnect-Request is sent."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        from app.radius.services.mikrotik_active_reconciler import ERR_SESSION_NOT_ACTIVE
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1")
            _seed_radacct(c, username="ghost", nas_ip="10.10.0.1",
                          session_id="OLD-1", framed_ip="10.5.0.5",
                          mac="AA:BB:CC:DD:EE:99")
        _patch_defaults(monkeypatch, fetch=lambda cfg: [])  # empty live list

        sent = []
        monkeypatch.setattr(radius_coa, "send_disconnect",
                            lambda **kw: sent.append(kw))
        res = radius_coa.disconnect_user(1, "ghost")
        assert not res.ok
        assert res.code_name == ERR_SESSION_NOT_ACTIVE
        assert sent == []                                # no malformed packet


def test_disconnect_api_down_falls_back_to_fresh_radacct(app, monkeypatch):
    """API unreachable + a FRESH radacct row → fall back and send from radacct
    (keeps disconnect working through transient API blips)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1", secret="s1")
            _seed_radacct(c, username="ali", nas_ip="10.10.0.1",
                          session_id="RAD-9", framed_ip="10.5.0.9",
                          mac="AA:BB:CC:DD:EE:01", age_min=1.0)  # fresh
        _patch_defaults(monkeypatch, fetch=lambda cfg: None)      # API down

        sent = []
        def _capture(**kw):
            sent.append(kw)
            return radius_coa.CoaResult(ok=True, code=41, code_name="Disconnect-ACK")
        monkeypatch.setattr(radius_coa, "send_disconnect", _capture)

        res = radius_coa.disconnect_user(1, "ali")
        assert res.ok, res.reply_message
        assert len(sent) == 1 and sent[0]["session_id"] == "RAD-9"


def test_disconnect_api_down_stale_radacct_refuses(app, monkeypatch):
    """API unreachable + only STALE radacct rows → fresh gate drops them → no
    packet, typed unreachable error (never sends on clearly-stale data)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        from app.radius.services.mikrotik_active_reconciler import ERR_ROUTER_UNREACHABLE
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1", secret="s1")
            _seed_radacct(c, username="ali", nas_ip="10.10.0.1",
                          session_id="RAD-OLD", framed_ip="10.5.0.9",
                          mac="AA:BB:CC:DD:EE:01", age_min=120.0)  # 2h stale
        _patch_defaults(monkeypatch, fetch=lambda cfg: None)

        sent = []
        monkeypatch.setattr(radius_coa, "send_disconnect",
                            lambda **kw: sent.append(kw))
        res = radius_coa.disconnect_user(1, "ali")
        assert not res.ok
        assert res.code_name == ERR_ROUTER_UNREACHABLE
        assert sent == []


def test_no_api_routers_uses_legacy_path(app, monkeypatch):
    """No API-capable routers configured (routers_queried==0) → the reconciler
    steps aside and the legacy radacct path runs unchanged (no-API deploys)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa
        import app.radius.services.mikrotik_active_reconciler as mar
        monkeypatch.setattr(mar, "_default_router_configs", lambda tid: [])
        with transaction() as c:
            _seed_nas(c, address="10.10.0.1", secret="s1")
            _seed_radacct(c, username="ali", nas_ip="10.10.0.1",
                          session_id="RAD-5", framed_ip="10.5.0.9",
                          mac="AA:BB:CC:DD:EE:01")
        sent = []
        def _capture(**kw):
            sent.append(kw)
            return radius_coa.CoaResult(ok=True, code=41, code_name="Disconnect-ACK")
        monkeypatch.setattr(radius_coa, "send_disconnect", _capture)
        res = radius_coa.disconnect_user(1, "ali")
        assert res.ok and len(sent) == 1 and sent[0]["session_id"] == "RAD-5"
