from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_BROADBAND_SCRIPT_PREVIEW,
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_VERIFICATION,
    STEP_STATUS_GENERATED,
    SetupWizardService,
    SetupWizardValidationError,
)
from app.radius.services.setup_wizard_broadband_planner import BroadbandBootstrapPlanner


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-bb-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


def test_broadband_smart_generation_contains_core_sections():
    plan = BroadbandBootstrapPlanner().plan(
        wizard_run_id=101,
        mode="smart",
        payload={"selected_interfaces": ["ether4"]},
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24", "10.20.30.0/24"],
    )
    assert "HOBERADIUS_SETUP:101:broadband" in plan.script_text
    assert "/interface pppoe-server server" in plan.script_text
    assert "/ppp profile" in plan.script_text
    assert "/tool ping 8.8.8.8 count=5" in plan.script_text


def test_broadband_pool_conflict_avoidance_manual_rejects_overlap():
    with pytest.raises(SetupWizardValidationError):
        BroadbandBootstrapPlanner().plan(
            wizard_run_id=102,
            mode="manual",
            payload={
                "selected_interfaces": ["ether3"],
                "local_address": "10.20.30.1",
                "remote_pool_cidr": "10.20.30.0/24",
            },
            blocked_interfaces=["ether1", "hr-wg"],
            blocked_network_cidrs=["10.20.30.0/24"],
        )


def test_broadband_nat_targets_remote_pool_only():
    plan = BroadbandBootstrapPlanner().plan(
        wizard_run_id=103,
        mode="manual",
        payload={
            "selected_interfaces": ["ether3"],
            "local_address": "10.77.90.1",
            "remote_pool_cidr": "10.77.90.0/24",
        },
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24"],
    )
    assert 'src-address="10.77.90.0/24"' in plan.script_text
    assert "out-interface-list" not in plan.script_text


def test_broadband_forbidden_commands_absent():
    plan = BroadbandBootstrapPlanner().plan(
        wizard_run_id=104,
        mode="smart",
        payload={"selected_interfaces": ["ether6"]},
        blocked_interfaces=["ether1", "hr-wg"],
        blocked_network_cidrs=["10.10.0.0/24"],
    )
    low = plan.script_text.lower()
    for token in ("/remove", "\nremove ", "/interface disable", "reset-configuration", "system reset"):
        assert token not in low


def test_broadband_generation_requires_vpn_verified(app):
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
        with pytest.raises(SetupWizardValidationError):
            svc.generate_broadband_script(
                tenant_id=1,
                run_id=run_id,
                mode="smart",
                payload={"selected_interfaces": ["ether3"]},
                blocked_network_cidrs=["10.10.0.0/24"],
            )


def test_broadband_generation_marks_step_generated(app):
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
        payload = {
            "selected_interfaces": ["ether3"],
            "local_address": "10.88.44.1",
            "remote_pool_cidr": "10.88.44.0/24",
        }
        plan = svc.generate_broadband_script(
            tenant_id=1,
            run_id=run_id,
            mode="manual",
            payload=payload,
            blocked_network_cidrs=["10.10.0.0/24", "10.20.30.0/24"],
        )
        assert "script_text" in plan
        step = svc.get_step(tenant_id=1, run_id=run_id, step_key=STEP_BROADBAND_SCRIPT_PREVIEW)
        assert step and step["status"] == STEP_STATUS_GENERATED
