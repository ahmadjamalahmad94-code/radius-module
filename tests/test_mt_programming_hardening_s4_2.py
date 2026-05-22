"""S4.2 — Programming planner consumes S4.1 safety verdicts.

Tests that:
  - Targeting a WireGuard interface → BLOCKED → plan risk row.
  - Targeting an interface with a default route → HIGH → risk row.
  - Targeting an interface inside the WG management subnet →
    BLOCKED → risk row.
  - Targeting a plain ether port → LOW → no extra risk.
  - When routes aren't provided, the classifier degrades
    gracefully (no crash, no false-positive WAN flag).
  - The same checks fire for PPPoE planner.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s4_2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _hotspot_spec(iface="ether2"):
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    return HotspotProgrammingSpec(
        interface=iface, cidr="192.168.10.0/24",
        hotspot_name="hs",
    )


def _pppoe_spec(iface="ether3"):
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec,
    )
    return PppoeProgrammingSpec(
        interface=iface, cidr="10.50.0.0/24",
        profile_name="p", service_name="s",
    )


# ─── Hotspot planner ──────────────────────────────────────────


def test_plan_hotspot_blocks_wireguard_interface(app):
    from app.radius.services.mt_programming import plan_hotspot
    spec = _hotspot_spec(iface="wg0")
    plan = plan_hotspot(
        {}, spec,
        existing_interfaces=[{"name": "wg0", "type": "wireguard"}],
        existing_addresses=[],
        existing_routes=[],
    )
    assert any("محظورة" in r for r in plan.risks)


def test_plan_hotspot_blocks_default_route_interface(app):
    from app.radius.services.mt_programming import plan_hotspot
    spec = _hotspot_spec(iface="ether1")
    plan = plan_hotspot(
        {}, spec,
        existing_interfaces=[{"name": "ether1", "type": "ether"}],
        existing_addresses=[],
        existing_routes=[{"dst-address": "0.0.0.0/0",
                          "gateway-interface": "ether1",
                          "disabled": "false"}],
    )
    assert any("عالية الخطورة" in r for r in plan.risks)


def test_plan_hotspot_blocks_iface_with_wg_subnet_address(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
    from app.radius.services.mt_programming import plan_hotspot
    spec = _hotspot_spec(iface="ether99")
    plan = plan_hotspot(
        {}, spec,
        existing_interfaces=[
            {"name": "ether99", "type": "ether"},
        ],
        existing_addresses=[
            # Address on this iface inside the management subnet.
            {"interface": "ether99",
             "address": "10.10.0.5/24"},
        ],
        existing_routes=[],
    )
    assert any("محظورة" in r for r in plan.risks)


def test_plan_hotspot_low_risk_iface_has_no_extra_risk_row(app):
    """A plain ether without addresses + without default route
    must NOT add new risk rows from the safety classifier. The
    existing planner contract still applies (validators, etc.)
    but no S4.1 noise."""
    from app.radius.services.mt_programming import plan_hotspot
    spec = _hotspot_spec(iface="ether5")
    plan = plan_hotspot(
        {}, spec,
        existing_interfaces=[{"name": "ether5", "type": "ether"}],
        existing_addresses=[],
        existing_routes=[],
    )
    safety_risks = [r for r in plan.risks
                    if "محظورة" in r or "عالية الخطورة" in r]
    assert safety_risks == []


def test_plan_hotspot_without_routes_does_not_crash(app):
    """If the K4 reader failed and routes is empty, the planner
    must still produce a plan — no AttributeError, no implicit
    WAN flag."""
    from app.radius.services.mt_programming import plan_hotspot
    spec = _hotspot_spec(iface="ether2")
    plan = plan_hotspot(
        {}, spec,
        existing_interfaces=[{"name": "ether2", "type": "ether"}],
        existing_addresses=[],
        # No routes passed.
    )
    assert plan.kind == "hotspot"


# ─── PPPoE planner ────────────────────────────────────────────


def test_plan_pppoe_blocks_wireguard_interface(app):
    from app.radius.services.mt_programming import plan_pppoe
    spec = _pppoe_spec(iface="wg0")
    plan = plan_pppoe(
        {}, spec,
        existing_interfaces=[{"name": "wg0", "type": "wireguard"}],
        existing_addresses=[],
        existing_routes=[],
    )
    assert any("محظورة" in r for r in plan.risks)


def test_plan_pppoe_blocks_default_route_interface(app):
    from app.radius.services.mt_programming import plan_pppoe
    spec = _pppoe_spec(iface="ether1")
    plan = plan_pppoe(
        {}, spec,
        existing_interfaces=[{"name": "ether1", "type": "ether"}],
        existing_addresses=[],
        existing_routes=[{"dst-address": "0.0.0.0/0",
                          "gateway-interface": "ether1",
                          "disabled": "false"}],
    )
    assert any("عالية الخطورة" in r for r in plan.risks)
