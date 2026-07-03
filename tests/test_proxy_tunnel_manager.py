"""Customer-side wg-radius tunnel + FreeRADIUS proxy-client bring-up
(CUSTOMER_RADIUS_TUNNEL_DESIGN §5/§6.2, Agent B).

Six categories from §8.B.6:

  1. keypair persistence — no regen when file exists.
  2. wg-conf rendering golden — Address /32 + AllowedIPs proxy /32 + keepalive.
  3. fingerprint no-op — identical heartbeat ⇒ no rewrite/reload trigger.
  4. proxy-client.conf golden + secret guard + reload-trigger touch.
  5. response-handler matrix —
       • enabled=false ⇒ no writes;
       • empty proxy_public_key ⇒ no-op + reason="proxy_pubkey_not_configured";
       • secret change alone ⇒ clients.conf REWRITTEN (fingerprint covers secret).
  6. heartbeat request carries the pubkey after generation.

All filesystem state goes into pytest's tmp_path — never touches /etc.
The radius_secret is never logged (we guard for substring leaks too).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ────────────────────── helpers ──────────────────────


def _mgr(tmp_path: Path, **kw):
    """Construct a manager pinned to tmp_path/state and tmp_path/clients."""
    from app.radius.services.proxy_tunnel_manager import ProxyTunnelManager
    return ProxyTunnelManager(
        state_dir=tmp_path / "state",
        clients_dir=tmp_path / "clients",
        **kw,
    )


def _valid_response(secret: str = "rR7-K2m-aBc"):
    """A complete §3.2 radius_tunnel block — non-empty, well-formed."""
    return {
        "enabled": True,
        "tunnel_ip": "10.200.5.2",
        "tunnel_cidr": 16,
        "proxy_public_key": "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=",
        "proxy_endpoint":   "proxy.hoberadius.com:51822",
        "proxy_tunnel_ip":  "10.200.0.1",
        "allowed_ips":      ["10.200.0.1/32"],
        "persistent_keepalive": 25,
        "radius_secret":    secret,
        "listen_ports":     {"auth": 1812, "acct": 1813},
    }


# ────────────────────── (1) keypair persistence ──────────────────────


def test_keypair_is_persisted_and_not_regenerated_on_second_call(tmp_path):
    m = _mgr(tmp_path)

    s1 = m.collect_request_state()
    pk1 = s1["public_key"]
    assert pk1, "first call must materialize a pubkey"
    assert re.fullmatch(r"[A-Za-z0-9+/]{43}=", pk1)
    priv_bytes_1 = m.private_key_path.read_text(encoding="ascii")

    # bump mtime so we'd detect any rewrite
    os.utime(m.private_key_path, (time.time() - 10, time.time() - 10))
    mtime_before = m.private_key_path.stat().st_mtime

    s2 = m.collect_request_state()
    assert s2["public_key"] == pk1, "pubkey changed on second call → keypair was regenerated"
    assert m.private_key_path.read_text(encoding="ascii") == priv_bytes_1
    assert m.private_key_path.stat().st_mtime == pytest.approx(mtime_before, abs=2), \
        "private key file was rewritten — manager is NOT idempotent on the keypair"


def test_public_key_matches_wg_pubkey_math(tmp_path):
    """Deriving from the stored privkey via X25519 must equal what
    collect_request_state() reports — proves the panel sees the same pubkey
    `wg pubkey < privkey` would emit on the host."""
    m = _mgr(tmp_path)
    reported = m.collect_request_state()["public_key"]
    from app.radius.services.proxy_tunnel_manager import _derive_public_key
    priv_b64 = m.private_key_path.read_text(encoding="ascii").strip()
    assert reported == _derive_public_key(priv_b64)


# ────────────────────── (2) wg-conf golden ──────────────────────


def test_wg_conf_golden_rendering(tmp_path):
    m = _mgr(tmp_path)
    res = m.apply_response(_valid_response())

    assert res.ok and "wg.write" in res.actions
    text = m.wg_conf_path.read_text(encoding="utf-8")

    # Address is /32 (the design's RADIUS_PER_HOST_ROUTE)
    assert re.search(r"^Address\s*=\s*10\.200\.5\.2/32\s*$", text, re.M), \
        "Address must be /32, not /16 (per-host route only)"
    # AllowedIPs is the proxy /32 only — customer never sees others
    assert re.search(r"^AllowedIPs\s*=\s*10\.200\.0\.1/32\s*$", text, re.M)
    # Keepalive carried verbatim
    assert re.search(r"^PersistentKeepalive\s*=\s*25\s*$", text, re.M)
    # Peer block carries the exact proxy pubkey + endpoint
    assert "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=" in text
    assert "proxy.hoberadius.com:51822" in text
    # The private key block is present (some valid base64 line follows)
    assert re.search(r"^PrivateKey\s*=\s*[A-Za-z0-9+/]{43}=\s*$", text, re.M)
    # File permissions are 0600 on POSIX (best-effort on Windows fakes)
    if hasattr(os, "stat"):
        mode = os.stat(m.wg_conf_path).st_mode & 0o777
        if os.name != "nt":
            assert mode == 0o600


# ────────────────────── (3) fingerprint no-op ──────────────────────


def test_identical_heartbeat_is_a_pure_noop(tmp_path):
    m = _mgr(tmp_path)
    r1 = m.apply_response(_valid_response())
    assert r1.ok and r1.actions, "first apply must write"
    fp = r1.fingerprint
    assert fp.startswith("sha256:")

    # Snapshot every file mtime
    paths = [m.wg_conf_path, m.clients_path, m.fingerprint_path,
             m.clients_dir / ".reload-trigger"]
    mtimes = {p: p.stat().st_mtime for p in paths if p.exists()}
    # Backdate so any touch would be obvious
    past = time.time() - 30
    for p in mtimes:
        os.utime(p, (past, past))
    mtimes = {p: p.stat().st_mtime for p in paths if p.exists()}

    r2 = m.apply_response(_valid_response())
    assert r2.ok
    assert r2.actions == (), f"expected no actions on identical input, got {r2.actions}"
    assert r2.reason == "fingerprint_unchanged"
    assert r2.fingerprint == fp

    for p, before in mtimes.items():
        assert p.stat().st_mtime == pytest.approx(before, abs=1), \
            f"{p} was rewritten despite identical input"


# ────────────────────── (4) proxy-client.conf golden + secret guard ──────────────────────


def test_proxy_client_conf_golden_and_reload_trigger(tmp_path):
    m = _mgr(tmp_path)
    secret = "panel-mints-this-no-operator-typing"
    m.apply_response(_valid_response(secret=secret))

    text = m.clients_path.read_text(encoding="utf-8")

    # The exact FreeRADIUS block shape the design requires.
    assert re.search(r"^client\s+radius-proxy\s*\{", text, re.M)
    assert re.search(r"^\s*ipaddr\s*=\s*10\.200\.0\.1\s*$", text, re.M)
    assert re.search(rf"^\s*secret\s*=\s*{re.escape(secret)}\s*$", text, re.M)
    assert re.search(r"^\s*nas_type\s*=\s*other\s*$", text, re.M)
    assert re.search(r"^\s*shortname\s*=\s*central-proxy\s*$", text, re.M)
    assert text.rstrip().endswith("}")

    trigger = m.clients_dir / ".reload-trigger"
    assert trigger.exists(), "reload-trigger must be touched after a write"


def test_unsafe_secret_chars_refuse_to_write_clients_conf(tmp_path, caplog):
    m = _mgr(tmp_path)
    caplog.set_level(logging.ERROR, logger="app.radius.services.proxy_tunnel_manager")

    bad_secrets = ['has"a"quote', "has}brace", "has\nnewline", "has\rreturn"]
    for bad in bad_secrets:
        res = m.apply_response(_valid_response(secret=bad))
        # clients.conf is not written
        assert "freeradius.write" not in res.actions, \
            f"clients.conf MUST refuse the unsafe secret {bad!r}"
        # The secret itself MUST NOT appear in any log record (no leakage)
        for rec in caplog.records:
            assert bad not in rec.getMessage(), \
                "secret value leaked into logs — never log secrets"
        caplog.clear()


def test_no_secret_value_leaks_into_step_result(tmp_path):
    """The TunnelStepResult is mirrored into attempt records / UI — verify
    none of its public fields can carry the secret."""
    m = _mgr(tmp_path)
    secret = "uniq-NEVER-LEAK-token-3kJ"
    res = m.apply_response(_valid_response(secret=secret))
    blob = json.dumps(res.as_dict(), ensure_ascii=False)
    assert secret not in blob


# ────────────────────── (5) response-handler matrix ──────────────────────


def test_response_handler_disabled_writes_nothing(tmp_path):
    m = _mgr(tmp_path)
    res = m.apply_response({**_valid_response(), "enabled": False})
    assert res.ok and res.actions == ()
    assert res.reason == "tunnel_disabled_by_panel"
    assert not m.wg_conf_path.exists()
    assert not m.clients_path.exists()


def test_response_handler_empty_proxy_pubkey_is_explicit_noop(tmp_path):
    m = _mgr(tmp_path)
    res = m.apply_response({**_valid_response(), "proxy_public_key": ""})
    assert res.ok and res.actions == ()
    assert res.reason == "proxy_pubkey_not_configured", \
        "owner hasn't pasted the proxy pubkey → must be a silent no-op, not a crash"


def test_response_handler_malformed_proxy_pubkey_is_rejected(tmp_path):
    m = _mgr(tmp_path)
    # Wrong length, missing trailing `=`
    res = m.apply_response({**_valid_response(), "proxy_public_key": "not-a-real-key"})
    assert not res.ok
    assert "invalid_proxy_pubkey" in res.warnings


def test_response_handler_secret_rotation_rewrites_clients_only(tmp_path):
    m = _mgr(tmp_path)
    m.apply_response(_valid_response(secret="alpha"))
    wg_mtime_before = m.wg_conf_path.stat().st_mtime

    # Force a clear gap so a rewrite is detectable
    os.utime(m.wg_conf_path, (time.time() - 30, time.time() - 30))
    wg_mtime_before = m.wg_conf_path.stat().st_mtime
    os.utime(m.clients_path, (time.time() - 30, time.time() - 30))
    fr_mtime_before = m.clients_path.stat().st_mtime

    res = m.apply_response(_valid_response(secret="beta"))
    assert res.ok
    assert "freeradius.write" in res.actions, \
        "secret rotation MUST rewrite proxy-client.conf"
    assert "wg.write" in res.actions, \
        "wg-conf also rewritten on any fingerprint change (we hash both)"
    assert m.clients_path.read_text(encoding="utf-8").find("secret      = beta") >= 0
    assert m.clients_path.stat().st_mtime > fr_mtime_before


# ────────────────────── (6) heartbeat request carries pubkey ──────────────────────


def test_heartbeat_request_payload_includes_wg_radius_pubkey(tmp_path, monkeypatch):
    """End-to-end: InstanceHealthService.build_payload() emits a
    `wg_radius` block whose `public_key` matches the one the manager would
    report — proves the wiring in build_payload."""
    monkeypatch.setenv("HOBERADIUS_TUNNEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR", str(tmp_path / "clients"))

    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService,
    )
    payload = InstanceHealthService().build_payload(tenant_id=1)
    wg = payload.get("wg_radius") or {}
    assert wg, "build_payload must include a wg_radius block (§3.1)"
    assert wg.get("public_key"), "pubkey must be reported once generated"
    assert re.fullmatch(r"[A-Za-z0-9+/]{43}=", wg["public_key"])
    # Re-asking the manager directly must agree (idempotent keypair):
    from app.radius.services.proxy_tunnel_manager import ProxyTunnelManager
    again = ProxyTunnelManager().collect_request_state()
    assert again["public_key"] == wg["public_key"]


def test_heartbeat_send_path_applies_tunnel_response(tmp_path, monkeypatch):
    """Simulate the panel returning a radius_tunnel block on the heartbeat
    response — send_heartbeat must invoke apply_response and surface the
    result without raising. We patch the transport so no network."""
    monkeypatch.setenv("HOBERADIUS_TUNNEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR", str(tmp_path / "clients"))
    # Bridge must be "enabled" to reach the POST path.
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "dummy-key-for-test")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_BASE_URL", "https://panel.local")

    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService,
    )

    svc = InstanceHealthService()
    # The DB-backed attempt log isn't the subject of this test; stub it.
    monkeypatch.setattr(svc, "record_attempt", lambda attempt: {"status": "sent"})

    fake_response_payload = {
        "ok": True,
        "status": "applied",
        "radius_tunnel": _valid_response(secret="from-panel-rt"),
    }
    with patch.object(
        svc.admin_client,
        "post_instance_heartbeat",
        return_value={
            "ok": True,
            "status": "sent",
            "response": fake_response_payload,
        },
    ):
        out = svc.send_heartbeat(tenant_id=1, dry_run=False)

    assert out["ok"] is True
    step = out["radius_tunnel_step"]
    assert step["ok"] is True
    assert "wg.write" in step["actions"]
    assert "freeradius.write" in step["actions"]
    # Real file landed in the customer-side path:
    assert (tmp_path / "clients" / "proxy-client.conf").exists()
    assert (tmp_path / "state" / "wg-radius.conf").exists()


# ────────────────────── (bonus) unprivileged degradation ──────────────────────


def test_state_dir_failure_does_not_raise_into_heartbeat(tmp_path, monkeypatch):
    """Customer-side: if /etc/hoberadius isn't writable we must still emit a
    heartbeat (with empty pubkey + empty fingerprint) instead of crashing
    the worker."""
    # Point at a file that can't become a dir — mkdir(parents=True) will fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("HOBERADIUS_TUNNEL_STATE_DIR", str(blocker / "subdir"))

    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService,
    )
    payload = InstanceHealthService().build_payload(tenant_id=1)
    wg = payload.get("wg_radius") or {}
    assert wg.get("public_key") == ""
    assert wg.get("interface_up") is False
    assert wg.get("config_fingerprint") == ""


# ─────────────── production regression: EACCES on the key write ───────────────
# The live failure was NOT dir-creation: /etc/hoberadius existed inside the
# container but was root-owned, so mkdir(exist_ok=True) succeeded and the raw
# PermissionError leaked from the tmp-file write in _write_atomic — escaping
# _ensure_keypair's RuntimeError contract and printing a full traceback every
# heartbeat (300s). These tests pin the exact scenario.


def _mgr(tmp_path):
    from app.radius.services.proxy_tunnel_manager import ProxyTunnelManager
    return ProxyTunnelManager(
        state_dir=tmp_path / "state",
        clients_dir=tmp_path / "clients",
        handshake_reader=lambda _p: None,
    )


def test_unwritable_key_write_does_not_raise_from_collect(tmp_path, monkeypatch):
    """Existing-but-unwritable state dir (the production case): the key WRITE
    fails with EACCES. collect_request_state must degrade (public_key="")
    and never raise."""
    m = _mgr(tmp_path)
    m.state_dir.mkdir(parents=True)

    def _deny(path, text, *, mode):
        raise PermissionError(13, "Permission denied", str(path) + ".tmp")

    monkeypatch.setattr(type(m), "_write_atomic", staticmethod(_deny))
    state = m.collect_request_state()          # must NOT raise
    assert state["public_key"] == ""
    assert state["config_fingerprint"] == ""


def test_ensure_keypair_wraps_write_eacces_in_runtimeerror(tmp_path, monkeypatch):
    """_ensure_keypair's contract: ANY unwritable-path failure surfaces as
    RuntimeError (so _write_wg_conf's `except RuntimeError` catches it too) —
    never a raw PermissionError."""
    import pytest as _pytest
    m = _mgr(tmp_path)
    m.state_dir.mkdir(parents=True)

    def _deny(path, text, *, mode):
        raise PermissionError(13, "Permission denied", str(path) + ".tmp")

    monkeypatch.setattr(type(m), "_write_atomic", staticmethod(_deny))
    with _pytest.raises(RuntimeError):
        m._ensure_keypair()


def test_permission_warning_is_throttled_not_per_call(tmp_path, monkeypatch, caplog):
    """The traceback used to print on EVERY heartbeat. Now: one WARNING per
    throttle window; repeat calls inside the window log nothing at WARNING."""
    import logging as _logging
    from app.radius.services import proxy_tunnel_manager as ptm
    monkeypatch.setattr(ptm, "_KEYPAIR_WARN_AT", [float("-inf")])
    m = _mgr(tmp_path)
    m.state_dir.mkdir(parents=True)

    def _deny(path, text, *, mode):
        raise PermissionError(13, "Permission denied", str(path) + ".tmp")

    monkeypatch.setattr(type(m), "_write_atomic", staticmethod(_deny))
    with caplog.at_level(_logging.WARNING, logger=ptm.__name__):
        for _ in range(5):
            m.collect_request_state()
    warns = [r for r in caplog.records if r.levelno >= _logging.WARNING]
    assert len(warns) == 1, f"expected exactly 1 throttled WARNING, got {len(warns)}"


def test_degraded_state_returns_last_known_pubkey(tmp_path, monkeypatch):
    """After one successful keygen, a later write failure (e.g. volume flipped
    read-only) must report the LAST-KNOWN public key — pages showing tunnel
    state keep rendering the real key instead of flapping to empty."""
    m = _mgr(tmp_path)
    ok = m.collect_request_state()
    assert ok["public_key"]

    # Simulate the key file disappearing + writes now denied.
    m.private_key_path.unlink()

    def _deny(path, text, *, mode):
        raise PermissionError(13, "Permission denied", str(path) + ".tmp")

    monkeypatch.setattr(type(m), "_write_atomic", staticmethod(_deny))
    degraded = m.collect_request_state()
    assert degraded["public_key"] == ok["public_key"]


def test_resolver_falls_back_to_instance_dir_when_etc_unwritable(monkeypatch):
    """No env override + /etc/hoberadius absent/unwritable (the standard
    container) → the state dir resolves to the instance fallback, never the
    root-owned path that spams EACCES."""
    from app.radius.services import proxy_tunnel_manager as ptm
    monkeypatch.delenv("HOBERADIUS_TUNNEL_STATE_DIR", raising=False)
    # Simulate the container: /etc/hoberadius has no key and is not writable.
    monkeypatch.setattr(ptm, "_DEFAULT_STATE_DIR", ptm.Path("/nonexistent-etc-hoberadius"))
    assert ptm._resolve_state_dir() == ptm._FALLBACK_STATE_DIR


def test_resolver_keeps_etc_when_key_already_there(tmp_path, monkeypatch):
    """Key stability: a server whose keypair already lives under
    /etc/hoberadius keeps using it (the reported pubkey must not rotate just
    because we added a fallback)."""
    from app.radius.services import proxy_tunnel_manager as ptm
    monkeypatch.delenv("HOBERADIUS_TUNNEL_STATE_DIR", raising=False)
    etc = tmp_path / "etc-hoberadius"
    etc.mkdir()
    (etc / "wg-radius.key").write_text("k\n", encoding="ascii")
    monkeypatch.setattr(ptm, "_DEFAULT_STATE_DIR", etc)
    assert ptm._resolve_state_dir() == etc
