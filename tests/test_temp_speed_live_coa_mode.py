# -*- coding: utf-8 -*-
"""Temporary speed = LIVE rate change via CoA (no disconnect) by default.

Owner spec:
  • persist the temp speed in the DB FIRST, then send a CoA-Request to
    nas_ip:3799 (never a Disconnect/PoD in the default path);
  • the CoA carries enough session-identifying attributes (User-Name,
    NAS-IP-Address, Acct-Session-Id, Framed-IP-Address, Calling-Station-Id)
    plus the new Mikrotik-Rate-Limit;
  • a `temporary_speed_apply_mode` setting (live_coa | disconnect_reauth),
    default live_coa;
  • on CoA failure never auto-disconnect — an optional manual reauth route
    exists instead;
  • the auth path still returns the temp speed on the next login (the rate
    is written to the subscriber's speed columns).
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_tslc_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_TEMP_SPEED_APPLY_MODE", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed(app, *, username="ahmad", down=2000, up=1000):
    with app.app_context():
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        db().execute(
            "INSERT INTO access_plans(tenant_id, name, speed_down_kbps, "
            "speed_up_kbps, created_at) VALUES (1,?,?,?,?)",
            ("Plan", down, up, now))
        db().execute(
            "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
            "download_speed_kbps, upload_speed_kbps, created_at) "
            "VALUES (1,?,?,?,?,?,?)",
            (username, "pw", 1, down, up, now))
        # an active session so CoA/PoD have a target
        db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
            "username, nasipaddress, framedipaddress, callingstationid, "
            "acctstarttime) VALUES (1,?,?,?,?,?,?,?)",
            ("s1", "u1", username, "10.10.0.9", "10.20.30.40",
             "AA:BB:CC:DD:EE:01", now))
        db().execute(
            "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
            "nas_type, enabled, created_at) VALUES (1,?,?,?,?,?,1,?)",
            ("r1", "10.10.0.9", "sec12345", "mikrotik", "hotspot", now))
        db().commit()


def _stub_coa(monkeypatch, *, rate_ok=True):
    """Record change_user_rate / disconnect_user calls without real UDP."""
    calls = {"rate": [], "disconnect": []}
    from app.radius.integration import radius_coa

    class _Res:
        def __init__(self, ok, code):
            self.ok, self.code_name = ok, code

    def _rate(tenant_id, username, *, new_rate_limit):
        calls["rate"].append((username, new_rate_limit))
        return _Res(rate_ok, "coa_ack" if rate_ok else "no_response")

    def _disc(tenant_id, username, *, session_ids=None):
        calls["disconnect"].append(username)
        return _Res(True, "disconnect_ack")

    monkeypatch.setattr(radius_coa, "change_user_rate", _rate)
    monkeypatch.setattr(radius_coa, "disconnect_user", _disc)
    return calls


# ── default mode ──────────────────────────────────────────────────────

def test_apply_mode_default_is_live_coa(app):
    with app.app_context():
        from app.radius.services.temp_speed import apply_mode, MODE_LIVE_COA
        assert apply_mode() == MODE_LIVE_COA


def test_live_coa_sends_rate_coa_and_never_disconnects(app, monkeypatch):
    _seed(app)
    calls = _stub_coa(monkeypatch)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        res = apply_temp_speed(tenant_id=1, actor="t", username="ahmad",
                               down_kbps=500, up_kbps=500, duration_minutes=30)
    assert res["mode"] == "live_coa"
    assert len(calls["rate"]) == 1                # a rate-CoA was sent
    assert calls["rate"][0][1] == "500k/500k"     # the new rate
    assert calls["disconnect"] == []              # NEVER disconnected


def test_db_persisted_first_so_auth_returns_temp_rate(app, monkeypatch):
    """Even if the live CoA fails, the throttle is written to the subscriber
    speed columns → the auth path returns it on the next login (spec #1/#8)."""
    _seed(app)
    _stub_coa(monkeypatch, rate_ok=False)         # CoA "fails"
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        apply_temp_speed(tenant_id=1, actor="t", username="ahmad",
                         down_kbps=512, up_kbps=256, duration_minutes=30)
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT temporary_speed, bandwidth_control_enabled, "
            "download_speed_kbps, upload_speed_kbps FROM subscribers "
            "WHERE username='ahmad'").fetchone()
    assert row["temporary_speed"] == 1
    assert row["bandwidth_control_enabled"] == 1
    assert row["download_speed_kbps"] == 512
    assert row["upload_speed_kbps"] == 256


def test_auth_reply_carries_temp_rate(app, monkeypatch):
    """The authorize path returns the temp Mikrotik-Rate-Limit on a fresh
    login (proves the temp window survives into RADIUS reply attributes)."""
    _seed(app)
    _stub_coa(monkeypatch)
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        apply_temp_speed(tenant_id=1, actor="t", username="ahmad",
                         down_kbps=768, up_kbps=384, duration_minutes=30)
        # clear the seeded live session so this fresh login isn't blocked by
        # the concurrent-device limit (we're testing the reply attrs only).
        from app.radius.db.connection import db
        db().execute("DELETE FROM radacct WHERE username='ahmad'")
        db().commit()
        from app.radius.services.policy_engine import authorize, AuthRequest
        dec = authorize(AuthRequest(username="ahmad", password="pw", tenant_id=1))
    assert dec.ok, dec.reason
    rate = (dec.reply_attrs or {}).get("Mikrotik-Rate-Limit", "")
    assert "384k/768k" in rate or "768k/384k" in rate, rate


# ── disconnect_reauth (setting + manual) ──────────────────────────────

def test_disconnect_reauth_mode_uses_pod_not_coa(app, monkeypatch):
    _seed(app)
    calls = _stub_coa(monkeypatch)
    with app.app_context():
        from app.radius.services.temp_speed import (
            apply_temp_speed, MODE_DISCONNECT_REAUTH)
        res = apply_temp_speed(tenant_id=1, actor="t", username="ahmad",
                               down_kbps=500, up_kbps=500, duration_minutes=30,
                               force_mode=MODE_DISCONNECT_REAUTH)
    assert res["mode"] == "disconnect_reauth"
    assert len(calls["disconnect"]) == 1          # a PoD was sent
    assert calls["rate"] == []                    # NOT a rate-CoA


def test_setting_switches_default_to_disconnect_reauth(app, monkeypatch):
    _seed(app)
    calls = _stub_coa(monkeypatch)
    monkeypatch.setenv("HOBERADIUS_TEMP_SPEED_APPLY_MODE", "disconnect_reauth")
    with app.app_context():
        from app.radius.services.temp_speed import apply_temp_speed
        res = apply_temp_speed(tenant_id=1, actor="t", username="ahmad",
                               down_kbps=500, up_kbps=500, duration_minutes=30)
    assert res["mode"] == "disconnect_reauth"
    assert len(calls["disconnect"]) == 1 and calls["rate"] == []


def test_reauth_route_is_registered(app):
    with app.app_context():
        from flask import url_for
        with app.test_request_context():
            assert url_for("radius.online_temp_speed_reauth")


def test_setting_is_in_registry_default_live_coa(app):
    from app.radius.core import env_settings
    spec = next((s for s in env_settings.REGISTRY
                 if s.key == "HOBERADIUS_TEMP_SPEED_APPLY_MODE"), None)
    assert spec is not None
    assert spec.default == "live_coa"
    vals = [v for v, _ in spec.options]
    assert vals == ["live_coa", "disconnect_reauth"]


# ── CoA packet carries the session-identifying attributes (spec #4) ────

def _parse_radius_attr_types(packet: bytes):
    types = []
    body = packet[20:]
    i = 0
    while i + 2 <= len(body):
        t, ln = body[i], body[i + 1]
        if ln < 2:
            break
        types.append((t, body[i + 2:i + ln]))
        i += ln
    return types


def test_coa_packet_carries_session_keys_and_rate(app, monkeypatch):
    """send_coa must include User-Name, NAS-IP, Acct-Session-Id, Framed-IP,
    Calling-Station-Id, and the Mikrotik-Rate-Limit VSA — so MT can match the
    live session and change its rate."""
    captured = {}

    class _FakeSock:
        def __init__(self, *a, **k): pass
        def settimeout(self, *a): pass
        def sendto(self, data, addr):
            captured["packet"] = data
            captured["addr"] = addr
        def recvfrom(self, n):
            # minimal CoA-ACK (code=44) so _parse_response returns ok
            p = captured["packet"]
            ident = p[1]
            return bytes([44, ident, 0, 20]) + bytes(16), captured["addr"]
        def close(self): pass

    from app.radius.integration import radius_coa
    monkeypatch.setattr(radius_coa.socket, "socket",
                        lambda *a, **k: _FakeSock())
    res = radius_coa.send_coa(
        nas_ip="10.10.0.9", nas_secret="sec12345", username="ahmad",
        session_id="s1", framed_ip="10.20.30.40",
        calling_station_id="AA:BB:CC:DD:EE:01", new_rate_limit="500k/500k")
    assert captured["addr"] == ("10.10.0.9", 3799)   # nas_ip:3799
    types = dict(_parse_radius_attr_types(captured["packet"]))
    assert 1 in types                                # User-Name
    assert 4 in types                                # NAS-IP-Address
    assert 44 in types                               # Acct-Session-Id
    assert 8 in types                                # Framed-IP-Address
    assert 31 in types                               # Calling-Station-Id
    # Vendor-Specific → Mikrotik (14988) subtype 8 (Rate-Limit)
    vsa = types.get(26, b"")
    assert vsa[:4] == struct.pack("!I", 14988)
    assert b"500k/500k" in vsa
    assert res.ok is True
