"""S4.1 — Interface safety classifier.

Pure-function tests. Each case names the signal it's pinning so
a future reader can map "what was this rule for" back to a
production incident.
"""
from __future__ import annotations

import pytest


# ─── WireGuard / management → BLOCKED ─────────────────────────


def test_wireguard_type_is_blocked():
    """RouterOS native type=wireguard → never reprogram."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_BLOCKED,
    )
    r = classify_interface({"name": "wg0", "type": "wireguard"})
    assert r.risk == RISK_BLOCKED


def test_name_wg_prefix_is_blocked():
    """Interface name carries 'wg' / 'wireguard' substring even
    when RouterOS type isn't set."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_BLOCKED,
    )
    r = classify_interface({"name": "wireguard-mgmt"})
    assert r.risk == RISK_BLOCKED


def test_address_inside_wg_subnet_is_blocked():
    """An ether interface that happens to carry a 10.10.0.x
    address is the management plane — must not be touched even
    if the name looks innocent."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_BLOCKED,
    )
    r = classify_interface(
        {"name": "ether99", "type": "ether"},
        addresses=[{"interface": "ether99",
                    "address": "10.10.0.5/24"}],
        wg_subnet="10.10.0.0/24",
    )
    assert r.risk == RISK_BLOCKED


def test_comment_says_management_is_blocked():
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_BLOCKED,
    )
    r = classify_interface(
        {"name": "ether1", "type": "ether",
         "comment": "hoberadius management — do not touch"},
    )
    assert r.risk == RISK_BLOCKED


# ─── Default route → HIGH ─────────────────────────────────────


def test_carries_default_route_is_high():
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_HIGH,
    )
    r = classify_interface(
        {"name": "ether1", "type": "ether"},
        routes=[{"dst-address": "0.0.0.0/0",
                  "gateway-interface": "ether1",
                  "disabled": "false"}],
    )
    assert r.risk == RISK_HIGH


def test_default_route_via_other_iface_is_not_high():
    """The default route belongs to ether1, but we're classifying
    ether2 — ether2 should not inherit the WAN verdict."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_HIGH,
    )
    r = classify_interface(
        {"name": "ether2", "type": "ether"},
        routes=[{"dst-address": "0.0.0.0/0",
                  "gateway-interface": "ether1",
                  "disabled": "false"}],
    )
    assert r.risk != RISK_HIGH


def test_disabled_default_route_is_ignored():
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_HIGH,
    )
    r = classify_interface(
        {"name": "ether1", "type": "ether"},
        routes=[{"dst-address": "0.0.0.0/0",
                  "gateway-interface": "ether1",
                  "disabled": "true"}],
    )
    assert r.risk != RISK_HIGH


def test_name_wan_uplink_is_high():
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_HIGH,
    )
    assert classify_interface(
        {"name": "WAN-1", "type": "ether"}).risk == RISK_HIGH
    assert classify_interface(
        {"name": "uplink", "type": "ether"}).risk == RISK_HIGH


# ─── Low / unknown ────────────────────────────────────────────


def test_plain_ether_with_no_signal_is_low():
    """The "obviously safe" common case — a bare ether port
    with nothing on it gets `low` so the planner can proceed
    without a confirmation gate."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_LOW,
    )
    r = classify_interface({"name": "ether5", "type": "ether"})
    assert r.risk == RISK_LOW


def test_unknown_type_stays_unknown():
    """If we can't tell what an interface is, we MUST be
    cautious — the directive's "unknown stays cautious"
    requirement."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_UNKNOWN,
    )
    r = classify_interface(
        {"name": "weird-iface", "type": "exotic-novel"},
        addresses=[{"interface": "weird-iface",
                    "address": "192.168.99.1/24"}],
    )
    assert r.risk == RISK_UNKNOWN


# ─── Highest-risk wins when multiple signals fire ─────────────


def test_wg_name_plus_default_route_remains_blocked():
    """Two signals would each fire (BLOCKED + HIGH); the worse
    one wins because operations against this iface can never
    be safe."""
    from app.radius.services.mt_interface_safety import (
        classify_interface, RISK_BLOCKED,
    )
    r = classify_interface(
        {"name": "wg-out", "type": "wireguard"},
        routes=[{"dst-address": "0.0.0.0/0",
                  "gateway-interface": "wg-out",
                  "disabled": "false"}],
    )
    assert r.risk == RISK_BLOCKED


# ─── Reasons surface for the operator ─────────────────────────


def test_reasons_are_arabic_and_describe_the_signal():
    from app.radius.services.mt_interface_safety import (
        classify_interface,
    )
    r = classify_interface({"name": "wg0", "type": "wireguard"})
    assert r.reasons, "blocked verdict must come with a reason"
    assert any("WireGuard" in s or "إدارة" in s
               for s in r.reasons)


def test_classify_many_handles_empty_lists():
    """The diagnostics tab calls this with whatever the router
    returned — including nothing. Should be a no-op, not raise."""
    from app.radius.services.mt_interface_safety import (
        classify_many,
    )
    assert classify_many([]) == []


def test_classify_many_returns_one_result_per_interface():
    from app.radius.services.mt_interface_safety import (
        classify_many, RISK_BLOCKED, RISK_LOW,
    )
    results = classify_many([
        {"name": "wg0", "type": "wireguard"},
        {"name": "ether2", "type": "ether"},
    ])
    by_name = {r.interface: r for r in results}
    assert by_name["wg0"].risk == RISK_BLOCKED
    assert by_name["ether2"].risk == RISK_LOW
