"""device_health planner — network calc + idempotent plan diff (pure, no app).

Run individually:  pytest tests/test_device_health_calc.py -q
"""
from __future__ import annotations

import pytest

from app.radius.services import device_health_planner as planner


# ── compute_network ────────────────────────────────────────────

def test_compute_network_spec_example():
    net = planner.compute_network("192.168.15.10", 24, 254)
    assert net["network_cidr"] == "192.168.15.0/24"
    assert net["gateway_address"] == "192.168.15.254/24"
    assert net["gateway_ip"] == "192.168.15.254"
    assert net["network_address"] == "192.168.15.0"
    assert net["prefix"] == 24


def test_compute_network_default_args():
    net = planner.compute_network("10.20.30.5")
    assert net["network_cidr"] == "10.20.30.0/24"
    assert net["gateway_address"] == "10.20.30.254/24"


def test_compute_network_other_prefix():
    net = planner.compute_network("172.16.4.9", 16, 1)
    assert net["network_cidr"] == "172.16.0.0/16"
    assert net["gateway_address"] == "172.16.0.1/16"


@pytest.mark.parametrize("bad", ["", "not-an-ip", "999.1.1.1", "192.168.1", "::1"])
def test_compute_network_invalid_ip(bad):
    with pytest.raises(planner.NetworkCalcError):
        planner.compute_network(bad, 24, 254)


@pytest.mark.parametrize("prefix", [0, 33, -1])
def test_compute_network_invalid_prefix(prefix):
    with pytest.raises(planner.NetworkCalcError):
        planner.compute_network("192.168.1.10", prefix, 254)


def test_compute_network_gateway_octet_out_of_range():
    # /24 broadcast is .255; octet 999 is outside the network.
    with pytest.raises(planner.NetworkCalcError):
        planner.compute_network("192.168.1.10", 24, 999)


# ── build_plan: intended (no router state) ─────────────────────

def test_build_plan_intended_marks_planned():
    plan = planner.build_plan(
        interface_name="ether2", ip_address="192.168.15.10")
    assert plan["ok"] and plan["valid"]
    assert plan["live"] is False
    kinds = {it["kind"]: it["action"] for it in plan["items"]}
    assert kinds == {"ip_address": "planned", "ip_binding": "planned",
                     "netwatch": "planned"}
    # Commands carry the computed network / gateway / host.
    cmds = " ".join(it["command"] for it in plan["items"])
    assert "192.168.15.254/24" in cmds       # /ip/address gateway
    # MT109 — الربط صار على عنوان الجهاز وحده: ربط الشبكة كلّها كان يفتح
    # النت لكلّ من يضع لنفسه عنوانًا ثابتًا فيها (بلا دخول ولا محاسبة).
    assert "192.168.15.0/24" not in cmds     # لا نربط الشبكة
    assert "ip-binding/add address=192.168.15.10" in cmds
    assert "host=192.168.15.10" in cmds      # netwatch host


def test_build_plan_requires_interface():
    plan = planner.build_plan(interface_name="", ip_address="192.168.15.10")
    assert plan["ok"] is False and plan["valid"] is False


def test_build_plan_invalid_ip_is_not_valid():
    plan = planner.build_plan(interface_name="ether2", ip_address="bad")
    assert plan["valid"] is False


# ── build_plan: live diff ──────────────────────────────────────

def _state(addresses=None, bindings=None, netwatch=None):
    return {"addresses": addresses or [], "bindings": bindings or [],
            "netwatch": netwatch or []}


def test_build_plan_live_all_missing_creates():
    plan = planner.build_plan(
        interface_name="ether2", ip_address="192.168.15.10",
        router_state=_state())
    assert plan["live"] is True
    actions = {it["kind"]: it["action"] for it in plan["items"]}
    assert actions == {"ip_address": "create", "ip_binding": "create",
                       "netwatch": "create"}


def test_build_plan_live_all_present_idempotent():
    state = _state(
        addresses=[{"address": "192.168.15.254/24", "interface": "ether2"}],
        bindings=[{"address": "192.168.15.10", "type": "bypassed"}],
        netwatch=[{"host": "192.168.15.10"}],
    )
    plan = planner.build_plan(
        interface_name="ether2", ip_address="192.168.15.10",
        router_state=state)
    actions = {it["kind"]: it["action"] for it in plan["items"]}
    assert actions == {"ip_address": "already_present",
                       "ip_binding": "already_present",
                       "netwatch": "already_present"}
    assert not plan["warnings"]


def test_build_plan_same_subnet_other_interface_warns():
    # Subnet already lives on ether5, not the requested ether2.
    state = _state(
        addresses=[{"address": "192.168.15.1/24", "interface": "ether5"}])
    plan = planner.build_plan(
        interface_name="ether2", ip_address="192.168.15.10",
        router_state=state)
    addr = next(it for it in plan["items"] if it["kind"] == "ip_address")
    assert addr["action"] == "create"          # not present on ether2
    assert any("غموض" in w or "مدخل آخر" in w for w in plan["warnings"])


def test_build_plan_netwatch_present_only():
    state = _state(netwatch=[{"host": "192.168.15.10"}])
    plan = planner.build_plan(
        interface_name="ether2", ip_address="192.168.15.10",
        router_state=state)
    actions = {it["kind"]: it["action"] for it in plan["items"]}
    assert actions["netwatch"] == "already_present"
    assert actions["ip_address"] == "create"
    assert actions["ip_binding"] == "create"
