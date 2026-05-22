"""Tests for the K1.2 NAS connection resolver."""
from __future__ import annotations

from app.radius.services.nas_connection import (
    is_vpn_mode,
    resolve_connection_address,
    resolve_connection_descriptor,
)


def test_direct_mode_returns_address():
    nas = {"connection_mode": "direct", "address": "203.0.113.5"}
    assert resolve_connection_address(nas) == "203.0.113.5"


def test_vpn_mode_returns_peer():
    nas = {
        "connection_mode": "vpn",
        "address": "203.0.113.5",
        "vpn_peer_address": "10.10.0.5",
    }
    assert resolve_connection_address(nas) == "10.10.0.5"


def test_vpn_mode_without_peer_falls_back():
    """A misconfigured row (vpn mode but no peer IP) must NOT
    silently dial nothing. Fall back to the public address."""
    nas = {
        "connection_mode": "vpn",
        "address": "203.0.113.5",
        "vpn_peer_address": "",
    }
    assert resolve_connection_address(nas) == "203.0.113.5"


def test_accepts_freeradius_nasname_key():
    nas = {"connection_mode": "direct", "nasname": "198.51.100.7"}
    assert resolve_connection_address(nas) == "198.51.100.7"


def test_accepts_alternate_keys():
    assert resolve_connection_address({"ip": "10.0.0.1"}) == "10.0.0.1"
    assert resolve_connection_address({"host": "router.lan"}) == "router.lan"


def test_empty_input_returns_empty_string():
    assert resolve_connection_address({}) == ""
    assert resolve_connection_address(None) == ""


def test_descriptor_carries_full_context():
    nas = {
        "address": "1.2.3.4",
        "connection_mode": "vpn",
        "vpn_peer_address": "10.10.0.5",
        "vpn_interface": "wg0",
        "vpn_public_key": "abc123",
        "vpn_last_handshake_ts": 1700000000,
        "vpn_assigned_ip": "10.10.0.5/24",
    }
    desc = resolve_connection_descriptor(nas)
    assert desc["address"] == "10.10.0.5"
    assert desc["mode"] == "vpn"
    assert desc["public_address"] == "1.2.3.4"
    assert desc["vpn_peer_address"] == "10.10.0.5"
    assert desc["vpn_interface"] == "wg0"
    assert desc["vpn_public_key"] == "abc123"
    assert desc["vpn_last_handshake_ts"] == 1700000000


def test_is_vpn_mode_flag():
    assert is_vpn_mode({"connection_mode": "vpn"}) is True
    assert is_vpn_mode({"connection_mode": "VPN"}) is True
    assert is_vpn_mode({"connection_mode": "direct"}) is False
    assert is_vpn_mode({}) is False  # default is direct


def test_unknown_mode_normalises_to_direct():
    """A row written by an older admin (no value) or by a typo
    must NOT be treated as VPN."""
    nas = {"connection_mode": "weird-value", "address": "1.1.1.1"}
    assert resolve_connection_address(nas) == "1.1.1.1"
    assert is_vpn_mode(nas) is False
