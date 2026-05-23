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
            "network_cidr": "10.77.50.0/24",
            "pool_range": "10.77.50.20-10.77.50.220",
            "gateway_ip": "10.77.50.1",
            "bridge_name": "hs-bridge",
            "profile_name": "hs-prof",
            "server_name": "hs-srv",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24", "10.20.30.0/24"],
    )
    assert "HOBERADIUS_SETUP:88:hotspot" in plan.script_text
    assert "/ip hotspot add name=\"hs-srv\"" in plan.script_text
    assert "/tool ping 8.8.8.8 count=5" in plan.script_text


def test_hotspot_wan_and_vpn_exclusion_enforced():
    with pytest.raises(SetupWizardValidationError):
        HotspotBootstrapPlanner().plan(
            wizard_run_id=89,
            mode="manual",
            payload={
                "selected_interfaces": ["ether1"],
                "network_cidr": "10.77.51.0/24",
                "pool_range": "10.77.51.20-10.77.51.220",
            },
            blocked_interfaces=["ether1", "hr-wg"],
            blocked_network_cidrs=["10.10.0.0/24"],
        )


def test_hotspot_smart_network_avoids_collisions():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=90,
        mode="smart",
        payload={"selected_interfaces": ["ether4"]},
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24", "10.20.30.0/24", "10.50.0.0/24"],
    )
    network = plan.computed["network_cidr"]
    assert network not in {"10.10.0.0/24", "10.20.30.0/24", "10.50.0.0/24"}


def test_hotspot_script_has_no_destructive_commands():
    plan = HotspotBootstrapPlanner().plan(
        wizard_run_id=91,
        mode="smart",
        payload={"selected_interfaces": ["ether5"]},
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
        names = {x["name"] for x in candidates}
        assert "ether1" not in names
        assert "hr-wg" not in names
        assert "ether2" in names


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
