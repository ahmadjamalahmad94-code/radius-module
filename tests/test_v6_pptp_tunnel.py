"""Tests for the v6 PPTP (Legacy/insecure) traffic-tunnel builder + renderer."""
from __future__ import annotations

import pytest

from app.radius.services import v6_tunnels as t


_R6 = {"name": "LAB-R6", "ros_version": "6.49.7"}
_BASE = {
    "pptp_server_host": "vpn.hoberadius.example",
    "username": "hr-r6",
    "password": "p",
}


def _sstp_plan():
    return {"tunnel_type": "sstp_mgmt", "interface_name": "sstp-hoberadius-mgmt",
            "add_default_route": False}


def test_pptp_plan_is_insecure_and_no_ipsec():
    plan = t.build_v6_pptp_traffic_plan(_R6, {**_BASE, "traffic_mode": "policy_routing",
                                              "source_clients": ["10.0.0.0/24"]})
    assert plan["tunnel_type"] == "pptp_traffic"
    assert plan["protocol"] == "pptp"
    assert plan["insecure"] is True
    assert plan["use_ipsec"] is False
    assert plan["interface_name"] == "pptp-hoberadius-traffic"
    assert any("PPTP غير آمن" in w for w in plan["warnings"])


def test_pptp_plan_does_not_require_ipsec_secret():
    # PPTP has no IPsec layer — building without an ipsec secret must work.
    plan = t.build_v6_pptp_traffic_plan(_R6, {**_BASE, "traffic_mode": "selected_pool",
                                              "selected_pool": "pool-1",
                                              "source_clients": ["192.168.88.0/24"]})
    assert plan["selected_pool"] == "pool-1"


def test_pptp_full_tunnel_requires_confirmation():
    with pytest.raises(t.TunnelPlanError):
        t.build_v6_pptp_traffic_plan(_R6, {**_BASE, "traffic_mode": "full_tunnel"})
    plan = t.build_v6_pptp_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "full_tunnel", "full_tunnel_confirmed": True})
    assert plan["owns_default_route"] is True


def test_pptp_script_shape():
    plan = t.build_v6_pptp_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "policy_routing", "source_clients": ["10.0.0.0/24"]})
    script = t.render_v6_pptp_traffic_script(plan)
    assert "pptp-hoberadius-traffic" in script
    assert "pptp-client" in script
    assert "use-ipsec" not in script  # PPTP has no IPsec
    assert "INSECURE" in script or "insecure" in script.lower()
    # same scoped routing model as L2TP
    assert "routing-mark=hoberadius_traffic_vpn" in script
    assert "src-address-list=hoberadius-vpn-traffic-clients" in script
    assert plan["add_default_route"] is False  # scoped → no default route


def test_pptp_disabled_mode_disables_interface():
    plan = t.build_v6_pptp_traffic_plan(_R6, {"traffic_mode": "disabled"})
    script = t.render_v6_pptp_traffic_script(plan)
    assert "disabled=yes" in script
    assert "pptp-client" in script


def test_conflict_analyzer_warns_pptp_not_blocking():
    traffic = t.build_v6_pptp_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "policy_routing", "source_clients": ["10.0.0.0/24"]})
    issues = t.analyze_tunnel_conflicts(1, "6", _sstp_plan(), traffic)
    codes = {i["code"] for i in issues}
    assert "pptp_insecure_legacy" in codes
    # PPTP must NOT trip the L2TP-only missing_ipsec_secret blocker
    assert "missing_ipsec_secret" not in codes
    assert not any(i["severity"] == "blocking" for i in issues)
