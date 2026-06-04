"""Pure-IPsec v6 exit tunnel (recommended encrypted exit; NOT L2TP).

Covers the planner + idempotent renderer in app/radius/services/v6_tunnels.py.
IPsec is policy-based: a phase-1 peer to the VPS + phase-2 encrypt policies for
the selected source traffic. It never creates an L2TP/PPP interface and never
installs a routing default route, so it cannot clash with SSTP management.
"""
from __future__ import annotations

import pytest

from app.radius.services import v6_tunnels as vt


V6 = {"name": "R6", "ros_version": "6.49.7"}


def test_ipsec_plan_scoped_does_not_own_default_route():
    plan = vt.build_v6_ipsec_traffic_plan(V6, {
        "ipsec_server_host": "198.51.100.5",
        "ipsec_secret": "s3cr3t",
        "traffic_mode": "policy_routing",
        "source_clients": ["192.168.88.0/24"],
    })
    assert plan["tunnel_type"] == "ipsec_traffic"
    assert plan["protocol"] == "ipsec"
    assert plan["use_ipsec"] is True
    assert plan["owns_default_route"] is False
    assert plan["interface_name"] == ""  # policy-based, no PPP interface


def test_ipsec_full_tunnel_requires_confirmation():
    with pytest.raises(vt.TunnelPlanError):
        vt.build_v6_ipsec_traffic_plan(V6, {
            "ipsec_server_host": "198.51.100.5",
            "ipsec_secret": "s3cr3t",
            "traffic_mode": "full_tunnel",
            "full_tunnel_confirmed": False,
        })


def test_ipsec_requires_secret_when_enabled():
    with pytest.raises(vt.TunnelPlanError):
        vt.build_v6_ipsec_traffic_plan(V6, {
            "ipsec_server_host": "198.51.100.5",
            "traffic_mode": "policy_routing",
            "source_clients": ["10.0.0.0/24"],
        })


def test_ipsec_v6_only_guard():
    with pytest.raises(vt.TunnelPlanError):
        vt.build_v6_ipsec_traffic_plan({"ros_version": ""}, {
            "ipsec_server_host": "198.51.100.5",
            "ipsec_secret": "x",
            "traffic_mode": "policy_routing",
        })


def test_ipsec_render_scoped_emits_peer_proposal_policy_no_l2tp():
    plan = vt.build_v6_ipsec_traffic_plan(V6, {
        "ipsec_server_host": "198.51.100.5",
        "ipsec_secret": "s3cr3t",
        "traffic_mode": "policy_routing",
        "source_clients": ["192.168.88.0/24", "10.10.0.0/24"],
    })
    script = vt.render_v6_ipsec_traffic_script(plan)
    assert "/ip ipsec proposal" in script
    assert "/ip ipsec peer" in script
    assert "/ip ipsec policy" in script
    assert "sa-dst-address=198.51.100.5" in script
    assert "src-address=192.168.88.0/24" in script
    assert "src-address=10.10.0.0/24" in script
    # encrypted exit — NOT an L2TP/PPP tunnel
    assert "l2tp-client" not in script
    # idempotent (find-by-comment / find-by-name)
    assert "find comment=" in script


def test_ipsec_render_full_tunnel_uses_catch_all_src():
    plan = vt.build_v6_ipsec_traffic_plan(V6, {
        "ipsec_server_host": "198.51.100.5",
        "ipsec_secret": "s3cr3t",
        "traffic_mode": "full_tunnel",
        "full_tunnel_confirmed": True,
    })
    script = vt.render_v6_ipsec_traffic_script(plan)
    assert "src-address=0.0.0.0/0" in script


def test_ipsec_render_disabled_disables_policy():
    plan = vt.build_v6_ipsec_traffic_plan(V6, {
        "ipsec_server_host": "198.51.100.5",
        "ipsec_secret": "s3cr3t",
        "traffic_mode": "disabled",
    })
    script = vt.render_v6_ipsec_traffic_script(plan)
    assert "disabled=yes" in script
    assert "/ip ipsec policy" in script


def test_protocol_to_type_mapping():
    from app.radius.services import routeros_caps as caps
    assert caps.traffic_protocol_to_type("ipsec") == "ipsec_traffic"
    assert caps.traffic_protocol_to_type("l2tp_ipsec") == "ipsec_traffic"  # alias
    assert caps.traffic_protocol_to_type("pptp") == "pptp_traffic"
    assert caps.traffic_protocol_to_type("") == "ipsec_traffic"  # default
    assert caps.TRAFFIC_PROTOCOLS == ("ipsec", "pptp")
