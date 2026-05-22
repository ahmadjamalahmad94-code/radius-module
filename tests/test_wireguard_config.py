"""Tests for K1.4 — WireGuard config generator."""
from __future__ import annotations

import pytest

from app.radius.services import wireguard_config as wg


def test_next_peer_ip_skips_server_and_taken():
    used = ["10.10.0.5", "10.10.0.7/24"]
    nxt = wg.next_peer_ip(used)
    # 10.10.0.1 = server, 10.10.0.5 + .7 taken → next free is .2
    assert nxt == "10.10.0.2"


def test_next_peer_ip_handles_no_used():
    nxt = wg.next_peer_ip([])
    assert nxt == "10.10.0.2"  # skip .1 (server)


def test_next_peer_ip_skips_explicit_server_ip():
    nxt = wg.next_peer_ip(["10.10.0.2", "10.10.0.3"], server_ip="10.10.0.5")
    # .2 + .3 taken, .5 reserved for server → .4
    assert nxt == "10.10.0.4"


def test_router_config_has_required_fields():
    text = wg.generate_router_config(
        nas_name="main-gw",
        peer_ip="10.10.0.5",
        server_public_key="serverpub==",
        server_endpoint="vps.example.com",
    )
    assert "/interface/wireguard add" in text
    assert "/interface/wireguard/peers add" in text
    assert "/ip/address add" in text
    assert "10.10.0.5/24" in text
    assert "serverpub==" in text
    assert "vps.example.com" in text
    assert "persistent-keepalive=25s" in text
    # Comment carries the NAS name
    assert "main-gw" in text


def test_server_block_has_peer_stanza():
    text = wg.generate_server_block(
        nas_name="branch-1",
        peer_ip="10.10.0.6",
        router_public_key="routerpub==",
    )
    assert "[Peer]" in text
    assert "PublicKey = routerpub==" in text
    assert "AllowedIPs = 10.10.0.6/32" in text
    # The live-add `wg set` hint is included as a comment
    assert "wg set wg0 peer" in text


def test_build_for_new_peer_combines_everything():
    bundle = wg.build_for_new_peer(
        nas_name="test-router",
        used_peer_ips=["10.10.0.5"],
        server_public_key="serverpub==",
        server_endpoint="vps.example.com",
    )
    assert bundle.peer_ip == "10.10.0.2"
    assert "test-router" in bundle.router_block
    assert "test-router" in bundle.server_block
    assert bundle.interface == "wg0"
    # No router pub key yet → note prompts the operator
    assert bundle.note != ""
    assert "vpn_public_key" in bundle.note
    # Without a router pub key, the server block shows a placeholder
    assert "<router public key here>" in bundle.server_block


def test_build_for_new_peer_with_router_pub_key():
    bundle = wg.build_for_new_peer(
        nas_name="r",
        used_peer_ips=[],
        server_public_key="srv",
        server_endpoint="x",
        router_public_key="routerpub_done==",
    )
    # Router pub key already known → no follow-up note
    assert bundle.note == ""
    assert "routerpub_done==" in bundle.server_block
    assert "<router public key here>" not in bundle.server_block


def test_next_peer_ip_exhausted_subnet_raises():
    used = [f"10.10.0.{i}" for i in range(2, 255)]
    with pytest.raises(RuntimeError, match="no free IPs"):
        wg.next_peer_ip(used)


def test_router_config_default_persistent_keepalive():
    text = wg.generate_router_config(
        nas_name="x",
        peer_ip="10.10.0.9",
        server_public_key="s",
        server_endpoint="e",
        keepalive_sec=30,
    )
    assert "persistent-keepalive=30s" in text


def test_generate_server_keypair_returns_two_base64_strings():
    priv, pub = wg.generate_server_keypair()
    import base64
    # base64 decode must succeed (i.e. valid base64)
    assert isinstance(priv, str) and isinstance(pub, str)
    base64.b64decode(priv)
    base64.b64decode(pub)
