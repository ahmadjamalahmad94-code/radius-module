"""Robust hotspot-page publish — root-cause fixes across the 3 transfer methods.

Covers:
  1. tool-fetch / HTTP: the router pulls over the MANAGEMENT TUNNEL, so the
     pull base must be the WG server IP (10.10.0.1), not the public/store IP.
     `resolve_mgmt_pull_base` + the route's `_resolve_pull_base` picks the
     tunnel base for VPN-mode routers and the public base for direct ones.
  2. FTP: the provisioning script grants the `ftp` policy to hr-api (and
     force-sets it on re-paste) so the FTP fallback can authenticate.
  3. API upload: post-write size verification detects a silent truncation and
     retries; the smart router only falls back to another channel on a
     transient wire error (never on a logical/permission error).
  4. Tunnel hardening: a conservative WG MTU (1380) is pinned so large writes
     don't blackhole and reset mid-transfer.
"""
from __future__ import annotations

import pytest

from app.radius.services import hotspot_templates as ht
from app.radius.services import hotspot_file_transfer as hft
from app.radius.services import mt_provisioner as mtp


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(ht._time, "sleep", lambda *_a, **_k: None)


# ─── 1) tool-fetch base = management-tunnel address ─────────────


def test_resolve_mgmt_pull_base_default(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_WG_SERVER_IP", raising=False)
    assert ht.resolve_mgmt_pull_base() == "http://10.10.0.1"


def test_resolve_mgmt_pull_base_env_override(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "10.20.0.1")
    assert ht.resolve_mgmt_pull_base() == "http://10.20.0.1"


def test_resolve_mgmt_pull_base_keeps_scheme(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "http://10.10.0.1:8080/")
    assert ht.resolve_mgmt_pull_base() == "http://10.10.0.1:8080"


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        return _FakeCursor(self._row)


def _patch_route(monkeypatch, *, mode, mgmt="http://10.10.0.1",
                 public="http://203.0.113.9"):
    from app.radius.routes import mt_login_designer as mld
    monkeypatch.setattr(mld, "_tid", lambda: 1)
    monkeypatch.setattr(mld, "db", lambda: _FakeDB({"connection_mode": mode}))
    monkeypatch.setattr(mld, "_auto_api_base", lambda: public)
    monkeypatch.setattr(mld.ht, "resolve_mgmt_pull_base", lambda: mgmt)
    return mld


def test_vpn_router_pulls_over_tunnel_base(monkeypatch):
    mld = _patch_route(monkeypatch, mode="vpn")
    # A tunnel-managed router must fetch from the WG server IP, NOT the public
    # store IP it cannot route to over the tunnel.
    assert mld._resolve_pull_base(7) == "http://10.10.0.1"


def test_direct_router_pulls_over_public_base(monkeypatch):
    mld = _patch_route(monkeypatch, mode="direct")
    assert mld._resolve_pull_base(7) == "http://203.0.113.9"


def test_vpn_router_falls_back_to_public_when_no_mgmt(monkeypatch):
    mld = _patch_route(monkeypatch, mode="vpn", mgmt="")
    assert mld._resolve_pull_base(7) == "http://203.0.113.9"


def test_fetch_config_builds_when_base_resolves(monkeypatch):
    mld = _patch_route(monkeypatch, mode="vpn")
    cfg = mld._fetch_config(7)
    assert cfg and cfg["base_url"] == "http://10.10.0.1"
    assert callable(cfg["stash_fn"])


def test_fetch_config_none_when_all_bases_unusable(monkeypatch):
    mld = _patch_route(monkeypatch, mode="direct", mgmt="", public="")
    assert mld._fetch_config(7) is None


# ─── 2) FTP policy granted in provisioning script ───────────────


def test_provisioning_script_grants_ftp_policy():
    script = mtp.render_routeros_script(
        nas_name="R1", api_user="hobe", api_password="pw",
        radius_secret="sec", server_ip="10.10.0.1", ros_version="7")
    assert "policy=read,write,ftp,api,test,winbox,sniff,sensitive,reboot" in script
    # Force-set every run so an existing hr-api (created before the grant) is
    # corrected too.
    assert '/user group set [find name="hr-api"] policy=read,write,ftp' in script


def test_provisioning_script_v6_also_grants_ftp():
    script = mtp.render_routeros_script(
        nas_name="R1", api_user="hobe", api_password="pw",
        radius_secret="sec", server_ip="10.10.0.1", ros_version="6")
    assert "policy=read,write,ftp,api" in script


# ─── 4) WG MTU hardening in the tunnel block ────────────────────


def test_wg_block_pins_conservative_mtu():
    blk = mtp.render_wg_block(
        nas_name="R1", router_private_key="KEY", server_pubkey="PUB",
        server_endpoint="1.2.3.4:51820", allowed_subnet="10.10.0.0/24",
        router_tunnel_ip="10.10.0.5")
    assert "mtu=1380" in blk
    # Applied idempotently to a fresh OR pre-existing interface.
    assert 'set [find name="hr-wg"] mtu=1380' in blk


# ─── 3a) post-write size verification ───────────────────────────


class _SizeRouter:
    def __init__(self, size):
        self.size = size

    def run(self, path, attrs=None):
        if path == "/file/print" and self.size is not None:
            return [{"reply": "!re",
                     "attrs": {"name": "hotspot/login.html",
                               "size": str(self.size)}}]
        return []


def test_verify_written_accepts_matching_size():
    assert ht._verify_written(_SizeRouter(1000), "hotspot/login.html", 1000)


def test_verify_written_flags_truncation():
    # Landed at 10 bytes but we wrote 1000 → truncated (silent reset).
    assert ht._verify_written(_SizeRouter(10), "hotspot/login.html", 1000) is False


def test_verify_written_unverifiable_when_no_size():
    class NoSize:
        def run(self, path, attrs=None):
            return [{"reply": "!re",
                     "attrs": {"name": "hotspot/login.html"}}]
    assert ht._verify_written(NoSize(), "hotspot/login.html", 1000) is True


def test_verify_written_unverifiable_on_print_error():
    class Err:
        def run(self, path, attrs=None):
            raise RuntimeError("print failed")
    assert ht._verify_written(Err(), "hotspot/login.html", 1000) is True


def test_verify_written_ignores_tiny_files():
    # < 64 bytes → don't second-guess allocation rounding.
    assert ht._verify_written(_SizeRouter(1), "hotspot/x.txt", 10) is True


class _TruncThenOkRouter:
    """/file/add succeeds but the router reports a truncated size on the first
    verify; after a reconnect+retry it reports the full size. Proves the
    verify→retry path recovers a silent truncation."""

    def __init__(self):
        self.adds = 0
        self.connects = 0

    def connect(self):
        self.connects += 1

    def close(self):
        pass

    def run(self, path, attrs=None):
        if path == "/file/print":
            if self.adds == 0:
                return []                      # nothing written yet
            size = 5 if self.adds == 1 else 10 ** 6
            return [{"reply": "!re",
                     "attrs": {"name": "hotspot/login.html",
                               "size": str(size)}}]
        if path == "/file/add":
            self.adds += 1
            return []
        return []


def test_silent_truncation_is_detected_and_retried():
    r = _TruncThenOkRouter()
    res = ht._put_file_once  # sanity: symbol exists
    out = ht._put_file(r, "hotspot/login.html", "<html>" + "x" * 500 + "</html>")
    assert out.ok is True
    assert r.adds == 2          # first (truncated) then a clean retry
    assert r.connects == 1      # reconnected once between attempts
    assert res is ht._put_file_once


# ─── 3b/4) smart routing: fall back only on transient errors ────


class _ApiRouter:
    def __init__(self, *, exc=None, fail_path="/file/add"):
        self.exc = exc
        self.fail_path = fail_path
        self.calls = []

    def connect(self):
        pass

    def close(self):
        pass

    def run(self, path, attrs=None):
        self.calls.append(path)
        if self.exc and path == self.fail_path:
            raise self.exc
        return []


def test_smart_small_reset_tries_fetch_and_aggregates_errors():
    # API resets (transient) → the fetch channel IS attempted; when it also
    # fails the error names BOTH channels (clear per-method diagnostics).
    r = _ApiRouter(exc=ConnectionResetError(104, "Connection reset by peer"))
    fetch = {"base_url": "http://10.10.0.1", "stash_fn": lambda d, ct: "T"}
    res = ht._put_file_smart(r, "hotspot/login.html", "small", fetch=fetch)
    assert res.ok is False
    assert "/tool/fetch" in r.calls                 # fetch was attempted
    assert "السحب عبر النفق" in res.error           # fetch failure surfaced
    assert "API:" in res.error                      # API failure surfaced


def test_smart_small_logical_error_does_not_try_other_channels():
    # A non-transient (permission) failure must NOT waste a fetch/FTP attempt —
    # another channel cannot fix a logical error.
    r = _ApiRouter(exc=RuntimeError("no perm"), fail_path="/file/print")
    fetch = {"base_url": "http://10.10.0.1", "stash_fn": lambda d, ct: "T"}
    res = ht._put_file_smart(r, "hotspot/login.html", "small", fetch=fetch)
    assert res.ok is False
    assert "/tool/fetch" not in r.calls
    assert "no perm" in res.error
