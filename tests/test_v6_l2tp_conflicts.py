"""Tests for the v6 L2TP/IPsec traffic tunnel + conflict analyzer."""
from __future__ import annotations

import pytest

from app.radius.services import v6_tunnels as t


_R6 = {"name": "LAB-R6", "ros_version": "6.49.7"}
_BASE = {
    "l2tp_server_host": "vpn.hoberadius.example",
    "username": "hr-r6",
    "password": "p",
    "ipsec_secret": "ike-secret",
}


def _sstp_plan(default_route=False):
    return {
        "tunnel_type": "sstp_mgmt", "interface_name": "sstp-hoberadius-mgmt",
        "add_default_route": default_route,
    }


# ── L2TP plan + script ──
def test_disabled_mode_builds_and_disables_interface():
    plan = t.build_v6_l2tp_ipsec_traffic_plan(_R6, {"traffic_mode": "disabled"})
    assert plan["enabled"] is False
    assert plan["owns_default_route"] is False
    script = t.render_v6_l2tp_ipsec_traffic_script(plan)
    assert "disabled=yes" in script


def test_full_tunnel_requires_confirmation():
    with pytest.raises(t.TunnelPlanError):
        t.build_v6_l2tp_ipsec_traffic_plan(_R6, {**_BASE, "traffic_mode": "full_tunnel"})
    plan = t.build_v6_l2tp_ipsec_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "full_tunnel", "full_tunnel_confirmed": True})
    assert plan["owns_default_route"] is True
    script = t.render_v6_l2tp_ipsec_traffic_script(plan)
    assert "use-ipsec=yes" in script
    assert "l2tp-hoberadius-traffic" in script
    assert "add-default-route=yes" in script
    assert "masquerade" in script


def test_missing_ipsec_secret_blocked():
    with pytest.raises(t.TunnelPlanError):
        t.build_v6_l2tp_ipsec_traffic_plan(
            _R6, {**_BASE, "ipsec_secret": "", "traffic_mode": "policy_routing"})


def test_selected_pool_requires_pool():
    with pytest.raises(t.TunnelPlanError):
        t.build_v6_l2tp_ipsec_traffic_plan(_R6, {**_BASE, "traffic_mode": "selected_pool"})
    plan = t.build_v6_l2tp_ipsec_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "selected_pool", "selected_pool": "pool-1",
              "source_clients": ["192.168.88.0/24"]})
    assert plan["selected_pool"] == "pool-1"


def test_policy_routing_uses_mark_and_scoped_nat_not_broad():
    plan = t.build_v6_l2tp_ipsec_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "policy_routing",
              "source_clients": ["192.168.88.0/24"]})
    script = t.render_v6_l2tp_ipsec_traffic_script(plan)
    assert "routing-mark=hoberadius_traffic_vpn" in script
    assert "mark-routing" in script
    assert "address-list=hoberadius-vpn-traffic-clients" in script or "list=hoberadius-vpn-traffic-clients" in script
    # scoped NAT references the address list (not a bare blanket masquerade)
    assert "src-address-list=hoberadius-vpn-traffic-clients" in script
    assert plan["add_default_route"] is False  # scoped mode never owns default


# ── conflict analyzer ──
def test_conflict_sstp_default_route_blocking():
    issues = t.analyze_tunnel_conflicts(1, "6", _sstp_plan(default_route=True), None)
    assert any(i["code"] == "sstp_default_route" and i["severity"] == "blocking" for i in issues)


def test_conflict_both_default_route_blocking():
    mgmt = _sstp_plan(default_route=True)
    traffic = t.build_v6_l2tp_ipsec_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "full_tunnel", "full_tunnel_confirmed": True})
    issues = t.analyze_tunnel_conflicts(1, "6", mgmt, traffic)
    assert any(i["code"] == "default_route_conflict" for i in issues)


def test_conflict_full_tunnel_warns_broad_nat():
    traffic = t.build_v6_l2tp_ipsec_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "full_tunnel", "full_tunnel_confirmed": True})
    issues = t.analyze_tunnel_conflicts(1, "6", _sstp_plan(), traffic)
    codes = {i["code"] for i in issues}
    assert "full_tunnel_high_risk" in codes
    assert "unsafe_broad_nat" in codes


def test_conflict_clean_plan_reports_ok():
    traffic = t.build_v6_l2tp_ipsec_traffic_plan(
        _R6, {**_BASE, "traffic_mode": "policy_routing", "source_clients": ["10.0.0.0/24"]})
    issues = t.analyze_tunnel_conflicts(1, "6", _sstp_plan(), traffic)
    # no blocking issues for a clean SSTP-mgmt + scoped-traffic plan
    assert not any(i["severity"] == "blocking" for i in issues)
