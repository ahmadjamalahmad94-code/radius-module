from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_VERIFICATION,
    STEP_STATUS_GENERATED,
    SetupWizardValidationError,
    SetupWizardService,
)
from app.radius.services.setup_wizard_hotspot_planner import HotspotBootstrapPlanner
from app.radius.services.setup_wizard_interface_contract import (
    InterfaceInfo,
    StaticInterfaceDiscovery,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-hs-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


def test_hotspot_manual_script_sections_and_validation():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=88,
        mode="manual",
        payload={
            "selected_interfaces": ["ether3"],
            "subnet_base": "10.20.0.0/16",
            "dns_name": "login.hoberadius.local",
            "router_vpn_ip": "10.10.0.3",
            "radius_server_ip": "10.10.0.1",
            "radius_secret": "radius-secret-ref-0001",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24", "10.20.30.0/24"],
    )
    assert '/ip address add address=10.20.3.1/24 interface=ether3 comment="HOBE_HOTSPOT_ether3"' in plan.script_text
    assert "/ip pool add name=pool-hotspot-ether3 ranges=10.20.3.10-10.20.3.254" in plan.script_text
    assert '/ip dhcp-server network add address=10.20.3.0/24 gateway=10.20.3.1 dns-server=10.20.3.1 comment="HOBE_HOTSPOT_ether3"' in plan.script_text
    assert '/radius add service=hotspot address=10.10.0.1 secret="radius-secret-ref-0001" authentication-port=1812 accounting-port=1813 src-address=10.10.0.3 timeout=3000ms comment="HOBERADIUS"' in plan.script_text
    assert "/interface bridge" not in plan.script_text
    assert "/tool ping 8.8.8.8 count=5" in plan.validation_commands


def test_hotspot_routeros7_profile_and_server_commands_match_direct_port_pattern():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=92,
        mode="manual",
        payload={
            "selected_interfaces": ["ether3", "ether4"],
            "subnet_base": "10.20.0.0/16",
            "router_vpn_ip": "10.10.0.3",
            "radius_secret": "radius-secret-ref-0001",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24"],
    )

    hotspot_add_lines = [
        line.strip()
        for line in plan.script_text.splitlines()
        if line.strip().startswith("/ip hotspot profile add")
        or line.strip().startswith("/ip hotspot add")
    ]

    assert hotspot_add_lines
    assert all(" comment=" not in line for line in hotspot_add_lines)
    assert "/ip hotspot profile add name=hsprof-ether3 hotspot-address=10.20.3.1 dns-name=login.hoberadius.local use-radius=yes radius-accounting=yes radius-interim-update=00:00:30 login-by=http-pap,cookie,mac-cookie" in plan.script_text
    assert "/ip hotspot add name=hotspot-ether4 interface=ether4 address-pool=pool-hotspot-ether4 profile=hsprof-ether4 disabled=no" in plan.script_text


def test_hotspot_nat_uses_wan_interface_list_per_port():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=93,
        mode="manual",
        payload={
            "selected_interfaces": ["ether3"],
            "subnet_base": "10.20.0.0/16",
            "router_vpn_ip": "10.10.0.3",
            "radius_secret": "radius-secret-ref-0001",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24"],
    )

    assert '/ip firewall nat add chain=srcnat src-address=10.20.3.0/24 out-interface-list=WAN action=masquerade comment="HOBE_HOTSPOT_ether3 NAT"' in plan.script_text
    assert "bridge" not in plan.script_text.lower()


def test_hotspot_wan_and_vpn_exclusion_enforced():
    with pytest.raises(SetupWizardValidationError):
        HotspotBootstrapPlanner().plan(
            wizard_run_id=89,
            mode="manual",
            payload={
                "selected_interfaces": ["ether1"],
                "router_vpn_ip": "10.10.0.3",
                "radius_secret": "radius-secret-ref-0001",
            },
            blocked_interfaces=["ether1", "hr-wg"],
            blocked_network_cidrs=["10.10.0.0/24"],
        )


def test_hotspot_smart_network_avoids_collisions_per_interface():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=90,
        mode="smart",
        payload={
            "selected_interfaces": ["ether4"],
            "subnet_base": "10.20.0.0/16",
            "router_vpn_ip": "10.10.0.3",
            "radius_secret": "radius-secret-ref-0001",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24", "10.20.4.0/24"],
    )
    network = plan.computed["port_plans"][0]["network_cidr"]
    assert network == "10.20.5.0/24"


def test_hotspot_script_has_no_destructive_commands():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=91,
        mode="smart",
        payload={
            "selected_interfaces": ["ether5"],
            "router_vpn_ip": "10.10.0.3",
            "radius_secret": "radius-secret-ref-0001",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24"],
    )
    low = plan.script_text.lower()
    for token in ("/remove", "\nremove ", "/interface disable", "reset-configuration"):
        assert token not in low


def test_interface_discovery_contract_and_service_gate(app):
    with app.app_context():
        svc = SetupWizardService(
            interface_discovery=StaticInterfaceDiscovery(
                [
                    InterfaceInfo(name="ether1"),
                    InterfaceInfo(name="ether2"),
                    InterfaceInfo(name="hr-wg"),
                ]
            )
        )
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]
        svc.set_internet_source(
            tenant_id=1,
            run_id=run_id,
            source_type="dhcp",
            selected_wan_interface="ether1",
            input_json={"interface": "ether1"},
        )
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)
        candidates = svc.get_interface_candidates(tenant_id=1, run_id=run_id)
        by_name = {x["name"]: x for x in candidates}
        assert by_name["ether1"]["safe"] is False
        assert by_name["ether1"]["excluded"] is True
        assert by_name["hr-wg"]["safe"] is False
        assert by_name["hr-wg"]["excluded"] is True
        assert by_name["ether2"]["safe"] is True


def test_interface_candidates_fallback_lists_common_eight_port_router(app):
    with app.app_context():
        svc = SetupWizardService()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]
        svc.set_internet_source(
            tenant_id=1,
            run_id=run_id,
            source_type="dhcp",
            selected_wan_interface="ether1",
            input_json={"interface": "ether1"},
        )
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)

        candidates = svc.get_interface_candidates(tenant_id=1, run_id=run_id)
        by_name = {x["name"]: x for x in candidates}

        for idx in range(1, 9):
            assert f"ether{idx}" in by_name
        assert by_name["ether1"]["safe"] is False
        assert by_name["ether8"]["safe"] is True


def test_generate_hotspot_script_updates_step_generated(app):
    with app.app_context():
        svc = SetupWizardService()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]
        svc.set_internet_source(
            tenant_id=1,
            run_id=run_id,
            source_type="dhcp",
            selected_wan_interface="ether1",
            input_json={"interface": "ether1"},
        )
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)
        plan = svc.generate_hotspot_script(
            tenant_id=1,
            run_id=run_id,
            mode="smart",
            payload={"selected_interfaces": ["ether3"]},
            blocked_network_cidrs=["10.10.0.0/24", "10.20.30.0/24"],
        )
        assert "script_text" in plan
        step = svc.get_step(tenant_id=1, run_id=run_id, step_key="hotspot_script_preview")
        assert step is not None and step["status"] == STEP_STATUS_GENERATED
