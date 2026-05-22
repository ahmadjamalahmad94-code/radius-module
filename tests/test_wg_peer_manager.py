"""M1 — peer manager unit tests.

Every test runs against a tmp peers.d so we never write into a
real /etc/hoberadius. Server config (subnet, pubkey, endpoint) is
provided via env vars per test.
"""
from __future__ import annotations

import base64
import ipaddress
import os
import re
from pathlib import Path

import pytest

from app.radius.services import wg_peer_manager as wpm


# ─── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path) -> dict:
    """Minimum env for the loader. Per-test overrides via the
    returned dict + monkeypatch."""
    peers_dir = tmp_path / "wg-peers.d"
    peers_dir.mkdir()
    monkeypatch.setenv(wpm.PEERS_DIR_ENV, str(peers_dir))
    monkeypatch.setenv(wpm.SUBNET_ENV, "10.10.0.0/24")
    monkeypatch.setenv(wpm.SERVER_IP_ENV, "10.10.0.1")
    monkeypatch.setenv(wpm.SERVER_PUBKEY_ENV, "TestServerPubKey00000000000000000000000000A=")
    monkeypatch.setenv(wpm.SERVER_ENDPOINT_ENV, "203.0.113.10:51820")
    monkeypatch.setenv(wpm.INTERFACE_ENV, "wg0")
    return {"peers_dir": peers_dir}


# ─── Config loading ──────────────────────────────────────────────


def test_load_config_happy_path(env):
    cfg = wpm.load_config()
    assert cfg.peers_dir == env["peers_dir"]
    assert cfg.subnet == ipaddress.ip_network("10.10.0.0/24")
    assert cfg.server_ip == ipaddress.IPv4Address("10.10.0.1")
    assert cfg.server_endpoint == "203.0.113.10:51820"
    assert cfg.interface == "wg0"


def test_load_config_rejects_missing_server_pubkey(env, monkeypatch):
    monkeypatch.setenv(wpm.SERVER_PUBKEY_ENV, "")
    with pytest.raises(ValueError, match="SERVER_PUBKEY"):
        wpm.load_config()


def test_load_config_rejects_missing_endpoint(env, monkeypatch):
    monkeypatch.setenv(wpm.SERVER_ENDPOINT_ENV, "")
    with pytest.raises(ValueError, match="SERVER_ENDPOINT"):
        wpm.load_config()


def test_load_config_rejects_server_ip_outside_subnet(env, monkeypatch):
    monkeypatch.setenv(wpm.SERVER_IP_ENV, "192.168.99.1")
    with pytest.raises(ValueError, match="outside subnet"):
        wpm.load_config()


def test_load_config_rejects_bad_subnet(env, monkeypatch):
    monkeypatch.setenv(wpm.SUBNET_ENV, "not-a-network")
    with pytest.raises(ValueError, match="not a valid IPv4 network"):
        wpm.load_config()


# ─── Keypair shape ───────────────────────────────────────────────


def test_generate_keypair_shape():
    priv, pub = wpm.generate_keypair()
    # Both are 44-char base64 (32 raw bytes → 44 chars including '=' pad).
    assert len(priv) == 44
    assert len(pub) == 44
    # Decode round-trip works → it's real base64.
    assert len(base64.b64decode(priv)) == 32
    assert len(base64.b64decode(pub)) == 32
    # The pair is deterministic — different priv must yield different pub.
    priv2, pub2 = wpm.generate_keypair()
    assert priv != priv2
    assert pub != pub2


# ─── IP allocation ───────────────────────────────────────────────


def test_allocate_next_ip_skips_server(env):
    ip = wpm.allocate_next_ip()
    # Server is 10.10.0.1; first peer should land at 10.10.0.2.
    assert str(ip) == "10.10.0.2"


def test_allocate_next_ip_skips_existing_peers(env):
    cfg = wpm.load_config()
    # Pre-seed peers.d with two existing peers.
    for name, ip in (("rt-a", "10.10.0.2"), ("rt-b", "10.10.0.3")):
        (cfg.peers_dir / f"{name}.conf").write_text(
            f"[Peer]\nPublicKey = K{ip}={'A'*38}\nAllowedIPs = {ip}/32\n",
        )
    nxt = wpm.allocate_next_ip()
    assert str(nxt) == "10.10.0.4"


def test_allocate_next_ip_exhaustion(env, monkeypatch):
    # Use a tiny /30 subnet (1 host) to easily exhaust.
    monkeypatch.setenv(wpm.SUBNET_ENV, "10.20.0.0/30")
    monkeypatch.setenv(wpm.SERVER_IP_ENV, "10.20.0.1")
    cfg = wpm.load_config()
    # /30 hosts: 10.20.0.1, 10.20.0.2. Server takes .1, peer takes .2.
    (cfg.peers_dir / "only.conf").write_text(
        "[Peer]\nPublicKey = X" + "A" * 43 + "\nAllowedIPs = 10.20.0.2/32\n",
    )
    with pytest.raises(RuntimeError, match="exhausted"):
        wpm.allocate_next_ip()


def test_allocate_skips_dot_prefixed_files(env):
    """Hidden files (e.g. the .canary we created during VPS testing)
    must NOT be counted as peers."""
    cfg = wpm.load_config()
    (cfg.peers_dir / ".canary").write_text(
        "[Peer]\nPublicKey = " + "A" * 43 + "=\nAllowedIPs = 10.10.0.2/32\n",
    )
    nxt = wpm.allocate_next_ip()
    # The .canary shouldn't have claimed .2; we should still get .2.
    assert str(nxt) == "10.10.0.2"


# ─── parse / list ────────────────────────────────────────────────


def test_parse_peer_file_recovers_base64_trailing_equals(env):
    cfg = wpm.load_config()
    pubkey = "g441IHtEhO214fE/zvzO0sd8JLBrr63qin2WHRXwFBY="  # real-world key
    path = cfg.peers_dir / "mt-vpn.conf"
    path.write_text(
        f"[Peer]\n# router comment\nPublicKey = {pubkey}\n"
        f"AllowedIPs = 10.10.0.2/32\nPersistentKeepalive = 25\n"
    )
    parsed = wpm.parse_peer_file(path)
    assert parsed["PublicKey"] == pubkey      # trailing '=' preserved
    assert parsed["AllowedIPs"] == "10.10.0.2/32"
    assert parsed["PersistentKeepalive"] == "25"
    assert parsed["name"] == "mt-vpn"


def test_list_managed_peers_sorted_and_filters_non_conf(env):
    cfg = wpm.load_config()
    for name in ("zebra", "alpha", "delta"):
        (cfg.peers_dir / f"{name}.conf").write_text(
            f"[Peer]\nPublicKey = K{name}{'A' * 38}\nAllowedIPs = 10.10.0.5/32\n",
        )
    # Non-.conf files must be ignored.
    (cfg.peers_dir / "readme.txt").write_text("ignore me")
    (cfg.peers_dir / ".hidden").write_text("also ignore")
    rows = wpm.list_managed_peers()
    names = [r["name"] for r in rows]
    assert names == ["alpha", "delta", "zebra"]


def test_list_managed_peers_empty_when_no_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(wpm.PEERS_DIR_ENV, str(tmp_path / "does-not-exist"))
    monkeypatch.setenv(wpm.SUBNET_ENV, "10.10.0.0/24")
    monkeypatch.setenv(wpm.SERVER_IP_ENV, "10.10.0.1")
    monkeypatch.setenv(wpm.SERVER_PUBKEY_ENV, "X" * 43 + "=")
    monkeypatch.setenv(wpm.SERVER_ENDPOINT_ENV, "1.1.1.1:51820")
    assert wpm.list_managed_peers() == []


# ─── provision / deprovision ─────────────────────────────────────


def test_provision_peer_writes_file_with_expected_block(env):
    res = wpm.provision_peer("MT-Office")
    assert res.peer_file.exists()
    body = res.peer_file.read_text()
    # Body contains every required field — no leftover placeholders.
    assert "[Peer]" in body
    assert res.router_public_key in body
    assert str(res.allowed_ip) in body
    assert "PersistentKeepalive = 25" in body
    # Private key is NOT written into the peer file (it goes to
    # the router only); the file holds only the server-side view.
    assert res.router_private_key not in body
    # Slug derived from the router name.
    assert res.slug == "MT-Office"


def test_provision_peer_assigns_next_free_ip(env):
    a = wpm.provision_peer("rt-1")
    b = wpm.provision_peer("rt-2")
    assert str(a.allowed_ip) == "10.10.0.2"
    assert str(b.allowed_ip) == "10.10.0.3"
    # Each peer file exists separately.
    assert a.peer_file != b.peer_file
    assert a.peer_file.is_file() and b.peer_file.is_file()


def test_provision_peer_refuses_to_overwrite(env):
    wpm.provision_peer("dup")
    with pytest.raises(ValueError, match="already exists"):
        wpm.provision_peer("dup")


def test_provision_peer_slugifies_unsafe_names(env):
    # Spaces collapse to '-', special chars dropped.
    res = wpm.provision_peer("  MT  Lab  #1  ")
    assert res.slug == "MT-Lab-1"
    assert res.peer_file.name == "MT-Lab-1.conf"


def test_provision_peer_rejects_completely_empty_name(env):
    with pytest.raises(ValueError, match="فارغ"):
        wpm.provision_peer("   ")


def test_provision_peer_rejects_path_traversal(env):
    # ".." / "/" / "\\" all get stripped by the slugifier — the
    # result is either empty or a clean stem, never an escape.
    cfg = wpm.load_config()
    res = wpm.provision_peer("../etc/passwd")
    assert ".." not in res.slug
    assert "/" not in res.slug
    # File still lives inside peers_dir.
    assert res.peer_file.parent == cfg.peers_dir


def test_provision_peer_returns_full_context_for_wizard(env):
    res = wpm.provision_peer("MT-Test")
    d = res.to_dict()
    # Every field the wizard's RouterOS-script renderer expects.
    for key in ("router_name", "slug", "peer_file", "router_private_key",
                "router_public_key", "allowed_ip", "server_pubkey",
                "server_endpoint", "server_ip_in_tunnel", "subnet",
                "interface", "keepalive_sec"):
        assert key in d, f"missing key {key!r}"
    assert d["allowed_ip"] == "10.10.0.2"
    assert d["server_ip_in_tunnel"] == "10.10.0.1"
    assert d["interface"] == "wg0"


def test_deprovision_peer_removes_file(env):
    res = wpm.provision_peer("disposable")
    assert res.peer_file.exists()
    assert wpm.deprovision_peer("disposable") is True
    assert not res.peer_file.exists()


def test_deprovision_peer_idempotent(env):
    # First call: no such peer → False.
    assert wpm.deprovision_peer("never-was") is False


# ─── N5: slug fallback for non-ASCII / digit-only names ──────────


def test_slugify_falls_back_for_arabic_only_name(env):
    """The wizard accepts Arabic names; the slugifier must not
    fail or produce something useless. After N5 it should yield
    a stable `nas-<6hex>` stem instead of just the leftover digit."""
    import re as _re
    res = wpm.provision_peer("سي سي ار تجريب 2")
    # Should NOT be just "2" — must use the fallback shape.
    assert res.slug != "2"
    assert _re.match(r"^nas-[0-9a-f]{6}$", res.slug), \
        f"unexpected slug: {res.slug!r}"
    # The peer file lives at nas-XXXXXX.conf, not 2.conf.
    assert res.peer_file.name.startswith("nas-")
    assert res.peer_file.name.endswith(".conf")
    # The full original name still goes into the peer file's
    # comment line so an operator inspecting peers.d can map back.
    assert "سي سي ار تجريب 2" in res.peer_file.read_text(encoding="utf-8")


def test_slugify_falls_back_for_pure_digits(env):
    res = wpm.provision_peer("12345")
    import re as _re
    assert _re.match(r"^nas-[0-9a-f]{6}$", res.slug)


def test_slugify_keeps_ascii_names_unchanged(env):
    """Names with Latin letters still get a meaningful slug — the
    fallback should only trigger when there's no usable letter."""
    res = wpm.provision_peer("MT-Office-1")
    assert res.slug == "MT-Office-1"


def test_slugify_empty_input_still_raises(env):
    """Truly empty input (whitespace only) doesn't trigger the
    fallback; it raises so the wizard rejects the form."""
    with pytest.raises(ValueError, match="فارغ"):
        wpm.provision_peer("   ")


def test_provision_then_deprovision_frees_ip(env):
    """The IP allocator must reclaim a freed slot — otherwise the
    254-IP /24 would slowly run out as routers churn."""
    a = wpm.provision_peer("rt-a")
    assert str(a.allowed_ip) == "10.10.0.2"
    wpm.deprovision_peer("rt-a")
    b = wpm.provision_peer("rt-b")
    assert str(b.allowed_ip) == "10.10.0.2"     # reused
