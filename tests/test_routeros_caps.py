"""Unit tests for the RouterOS version → capability matrix.

Pure functions — no DB/app fixture needed, so these run fast and pin down
the v6/v7 behaviour the setup wizard + provisioner rely on.
"""
from __future__ import annotations

import pytest

from app.radius.services import routeros_caps as caps


@pytest.mark.parametrize("value,expected", [
    ("6", 6),
    ("7", 7),
    (6, 6),
    (7, 7),
    ("6.49.7", 6),
    ("7.11.2 (stable)", 7),
    ("RouterOS 7.15", 7),
    ("", None),
    (None, None),
    ("stable", None),
    (0, None),
])
def test_parse_major(value, expected):
    assert caps.parse_major(value) == expected


@pytest.mark.parametrize("value,wg", [
    ("7", True),
    ("7.11.2", True),
    (7, True),
    ("6", False),
    ("6.49.7", False),
    (6, False),
    ("", False),       # unknown → conservative (no WireGuard)
    (None, False),
])
def test_supports_wireguard(value, wg):
    assert caps.supports_wireguard(value) is wg


def test_requires_direct_address_is_inverse_of_wireguard():
    assert caps.requires_direct_address("6") is True
    assert caps.requires_direct_address("7") is False
    assert caps.requires_direct_address("") is True  # unknown → safe default


def test_connection_modes():
    assert caps.connection_modes("7") == ["vpn", "direct", "dhcp_push"]
    assert caps.connection_modes("6") == ["direct", "dhcp_push"]
    assert "vpn" not in caps.connection_modes("6")


def test_detect_major_from_resource_dict_and_list():
    assert caps.detect_major_from_resource({"version": "6.49.7 (stable)"}) == 6
    assert caps.detect_major_from_resource([{"version": "7.11.2"}]) == 7
    assert caps.detect_major_from_resource({}) is None
    assert caps.detect_major_from_resource([]) is None
    assert caps.detect_major_from_resource(None) is None


def test_summary_shapes():
    v7 = caps.summary("7.11")
    assert v7["major"] == 7 and v7["wireguard"] is True
    assert v7["requires_direct_address"] is False
    assert "WireGuard" in v7["note_ar"]

    v6 = caps.summary("6.49.7")
    assert v6["major"] == 6 and v6["wireguard"] is False
    assert v6["requires_direct_address"] is True
    assert "دفع DHCP" in v6["note_ar"]


# ── v6 SSTP + L2TP/IPsec capability matrix ──
@pytest.mark.parametrize("value,expected", [
    ("6.49.7", "6"), ("7.11.2 (stable)", "7"), ("7.15rc", "7"),
    (6, "6"), (7, "7"), ("invalid", None), ("", None), (None, None),
])
def test_parse_routeros_major_returns_string(value, expected):
    assert caps.parse_routeros_major(value) == expected


@pytest.mark.parametrize("v,sstp,l2tp", [
    ("6", True, True), ("6.49.7", True, True),
    ("7", True, True), ("7.11", True, True),
    ("", False, False), (None, False, False),
])
def test_supports_sstp_and_l2tp(v, sstp, l2tp):
    assert caps.supports_sstp_mgmt(v) is sstp
    assert caps.supports_l2tp_ipsec_traffic(v) is l2tp


def test_recommended_tunnels_per_version():
    assert caps.recommended_management_tunnel("7") == "wireguard"
    assert caps.recommended_management_tunnel("6") == "sstp_mgmt"
    assert caps.recommended_management_tunnel("") == "manual_review"
    assert caps.recommended_traffic_tunnel("7") == "wireguard_traffic"
    assert caps.recommended_traffic_tunnel("6") == "l2tp_ipsec_traffic"
    assert caps.recommended_traffic_tunnel("") == "manual_review"


def test_connection_modes_for_version_v6_has_no_wireguard():
    v6 = caps.connection_modes_for_version("6")
    assert v6 == ["sstp_mgmt", "l2tp_ipsec_traffic", "direct", "dhcp_push"]
    assert "vpn" not in v6
    assert caps.connection_modes_for_version("7") == ["vpn", "direct", "dhcp_push"]
    assert caps.connection_modes_for_version("") == ["direct", "dhcp_push"]


def test_tunnel_capabilities_matrix():
    v6 = caps.tunnel_capabilities("6.49.7")
    assert v6["supports_wireguard"] is False
    assert v6["supports_sstp_mgmt"] is True
    assert v6["recommended_management_tunnel"] == "sstp_mgmt"
    v7 = caps.tunnel_capabilities("7.11")
    assert v7["supports_wireguard"] is True
    assert v7["recommended_management_tunnel"] == "wireguard"


# ── validate_connection_plan ──
def test_validate_v6_wireguard_blocked():
    res = caps.validate_connection_plan("6", "wireguard", "none")
    assert res["valid"] is False
    assert any(e["code"] == "routeros_v6_wireguard_not_supported" for e in res["errors"])


def test_validate_v7_wireguard_valid():
    res = caps.validate_connection_plan("7", "wireguard", "none")
    assert res["valid"] is True
    assert res["errors"] == []


def test_validate_v6_sstp_management_valid():
    res = caps.validate_connection_plan("6", "sstp_mgmt", "none")
    assert res["valid"] is True


def test_validate_v6_sstp_plus_l2tp_traffic_valid():
    res = caps.validate_connection_plan("6", "sstp_mgmt", "l2tp_ipsec_traffic")
    assert res["valid"] is True


def test_validate_sstp_default_route_blocked():
    res = caps.validate_connection_plan(
        "6", "sstp_mgmt", "none", sstp_sets_default_route=True)
    assert res["valid"] is False
    assert any(e["code"] == "sstp_must_not_own_default_route" for e in res["errors"])


def test_validate_both_default_route_blocked():
    res = caps.validate_connection_plan(
        "6", "sstp_mgmt", "l2tp_ipsec_traffic",
        sstp_sets_default_route=True, traffic_owns_default_route=True)
    codes = {e["code"] for e in res["errors"]}
    assert "default_route_conflict" in codes


def test_validate_full_tunnel_requires_confirmation():
    blocked = caps.validate_connection_plan(
        "6", "sstp_mgmt", "l2tp_ipsec_traffic", traffic_mode="full_tunnel")
    assert any(e["code"] == "full_tunnel_requires_confirmation" for e in blocked["errors"])
    ok = caps.validate_connection_plan(
        "6", "sstp_mgmt", "l2tp_ipsec_traffic",
        traffic_mode="full_tunnel", full_tunnel_confirmed=True)
    assert all(e["code"] != "full_tunnel_requires_confirmation" for e in ok["errors"])


def test_validate_selected_pool_requires_pool():
    blocked = caps.validate_connection_plan(
        "6", "sstp_mgmt", "l2tp_ipsec_traffic", traffic_mode="selected_pool")
    assert any(e["code"] == "missing_selected_pool" for e in blocked["errors"])
    ok = caps.validate_connection_plan(
        "6", "sstp_mgmt", "l2tp_ipsec_traffic",
        traffic_mode="selected_pool", selected_pool="pool-1")
    assert all(e["code"] != "missing_selected_pool" for e in ok["errors"])


def test_validate_traffic_without_management_blocked():
    res = caps.validate_connection_plan("6", "none", "l2tp_ipsec_traffic")
    assert any(e["code"] == "management_tunnel_would_be_lost" for e in res["errors"])


def test_validate_sstp_on_v7_warns_not_blocks():
    res = caps.validate_connection_plan("7", "sstp_mgmt", "none")
    assert res["valid"] is True
    assert any(w["code"] == "sstp_on_v7_not_recommended" for w in res["warnings"])


# ── PPTP (Legacy/insecure traffic option) ──
@pytest.mark.parametrize("v,ok", [
    ("6", True), ("6.49.7", True), ("7", True), ("", False), (None, False),
])
def test_supports_pptp_traffic(v, ok):
    assert caps.supports_pptp_traffic(v) is ok


def test_pptp_is_allowed_but_warned_never_recommended():
    res = caps.validate_connection_plan("6", "sstp_mgmt", "pptp_traffic")
    # allowed (not blocking) ...
    assert res["valid"] is True
    # ... but always warns about insecurity
    assert any(w["code"] == "pptp_insecure_legacy" for w in res["warnings"])
    # and PPTP is NEVER the recommended/default traffic tunnel
    assert caps.recommended_traffic_tunnel("6") == "l2tp_ipsec_traffic"
    assert caps.recommended_traffic_tunnel("6") != "pptp_traffic"


def test_pptp_in_traffic_vocabulary_and_capabilities():
    assert "pptp_traffic" in caps.TRAFFIC_TUNNEL_TYPES
    assert "pptp" in caps.TRAFFIC_PROTOCOLS
    matrix = caps.tunnel_capabilities("6.49.7")
    assert matrix["supports_pptp_traffic"] is True


def test_provisioner_wg_guard_uses_caps():
    """render_wg_block must refuse v6 and accept v7 (via the central helper)."""
    from app.radius.services import mt_provisioner as prov

    with pytest.raises(ValueError):
        prov.render_wg_block(
            nas_name="r6", router_private_key="k", server_pubkey="p",
            server_endpoint="1.2.3.4:13231", allowed_subnet="10.10.0.0/24",
            router_tunnel_ip="10.10.0.2", ros_version="6",
        )
    # v7 with full creds should NOT raise the version guard
    block = prov.render_wg_block(
        nas_name="r7", router_private_key="k", server_pubkey="p",
        server_endpoint="1.2.3.4:13231", allowed_subnet="10.10.0.0/24",
        router_tunnel_ip="10.10.0.2", ros_version="7",
    )
    assert "wireguard" in block.lower()
