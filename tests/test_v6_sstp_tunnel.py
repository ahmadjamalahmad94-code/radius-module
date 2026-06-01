"""Tests for the RouterOS v6 SSTP management-tunnel planner + generator.

Pure functions — no DB/app fixture. Pin the management-only invariants the
strategy depends on (no default route, scoped routes, clear comments).
"""
from __future__ import annotations

import pytest

from app.radius.services import v6_tunnels as t


_ROUTER = {"name": "LAB-R6", "ros_version": "6.49.7"}
_SETTINGS = {
    "sstp_server_host": "vpn.hoberadius.example",
    "username": "hr-r6",
    "password": "s3cr3t-pass",
    "mgmt_subnet": "10.10.0.0/24",
}


def test_plan_is_management_only_no_default_route():
    plan = t.build_v6_sstp_management_plan(_ROUTER, _SETTINGS)
    assert plan["tunnel_type"] == "sstp_mgmt"
    assert plan["interface_name"] == "sstp-hoberadius-mgmt"
    assert plan["add_default_route"] is False
    assert plan["ros_version"] == "6"


def test_plan_rejects_missing_creds():
    with pytest.raises(t.TunnelPlanError):
        t.build_v6_sstp_management_plan(_ROUTER, {"sstp_server_host": "x"})


def test_plan_allows_sstp_on_v7_as_fallback_with_warning():
    # SSTP is a valid (optional) management fallback on v7, but WireGuard is
    # preferred — the plan must succeed and carry the advisory warning.
    plan = t.build_v6_sstp_management_plan({"ros_version": "7.11"}, _SETTINGS)
    assert plan["tunnel_type"] == "sstp_mgmt"
    assert any("WireGuard" in w for w in plan["warnings"])


def test_plan_warns_when_cert_verification_off():
    plan = t.build_v6_sstp_management_plan(_ROUTER, _SETTINGS)
    assert plan["verify_certificate"] is False
    assert any("verify-server-certificate=no" in w for w in plan["warnings"])
    plan_on = t.build_v6_sstp_management_plan(
        _ROUTER, {**_SETTINGS, "verify_certificate": True})
    assert plan_on["verify_certificate"] is True


def test_script_contains_required_markers():
    script = t.render_v6_sstp_management_script(
        t.build_v6_sstp_management_plan(_ROUTER, _SETTINGS))
    assert "sstp-hoberadius-mgmt" in script
    assert "add-default-route=no" in script
    assert "HobeRadius management tunnel - do not use for subscriber traffic" in script
    # management only → never a default route anywhere
    assert "0.0.0.0/0" not in script
    # idempotent shape
    assert "sstp-client find name=" in script


def test_script_honours_cert_setting():
    off = t.render_v6_sstp_management_script(
        t.build_v6_sstp_management_plan(_ROUTER, _SETTINGS))
    assert "verify-server-certificate=no" in off
    on = t.render_v6_sstp_management_script(
        t.build_v6_sstp_management_plan(_ROUTER, {**_SETTINGS, "verify_certificate": True}))
    assert "verify-server-certificate=yes" in on


def test_render_refuses_default_route_plan():
    plan = t.build_v6_sstp_management_plan(_ROUTER, _SETTINGS)
    plan["add_default_route"] = True
    with pytest.raises(t.TunnelPlanError):
        t.render_v6_sstp_management_script(plan)


def test_mgmt_routes_are_scoped_never_default():
    plan = t.build_v6_sstp_management_plan(
        _ROUTER, {**_SETTINGS, "mgmt_routes": ["10.10.0.0/24", "0.0.0.0/0"]})
    # the 0.0.0.0/0 entry is filtered out of the plan
    assert "0.0.0.0/0" not in plan["mgmt_routes"]
    script = t.render_v6_sstp_management_script(plan)
    assert "10.10.0.0/24" in script
    assert "0.0.0.0/0" not in script
