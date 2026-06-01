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
