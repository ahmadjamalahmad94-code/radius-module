from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_HOTSPOT_CHOICE,
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_SCRIPT_PREVIEW,
    STEP_VPN_RADIUS_VERIFICATION,
    STEP_STATUS_GENERATED,
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_verification import (
    SetupDiagnosticsService,
    SetupVerificationService,
)
from app.radius.services.setup_wizard_vpn_radius_planner import (
    VpnRadiusBootstrapPlanner,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-vpn-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


def _payload() -> dict[str, str]:
    return {
        "wg_interface_name": "hr-wg",
        "peer_name": "vps-peer",
        "router_vpn_ip": "10.10.0.3",
        "vps_vpn_ip": "10.10.0.1",
        "allowed_address": "10.10.0.1/32",
        "vps_public_endpoint": "187.77.70.18",
        "endpoint_port": 51820,
        "radius_server_ip": "10.10.0.1",
        "radius_secret": "Secret!123",
        "api_username": "hr_api_test",
        "server_public_key": "A" * 43 + "=",
    }


def test_vpn_radius_planner_generation_and_tagging():
    plan = VpnRadiusBootstrapPlanner().plan(wizard_run_id=77, payload=_payload())
    assert "HOBERADIUS_SETUP:77:vpn" in plan.script_text
    assert "HOBERADIUS_SETUP:77:radius" in plan.script_text
    assert "HOBERADIUS_SETUP:77:api" in plan.script_text
    assert 'public-key="' in plan.script_text
    assert "/interface wireguard print detail" in plan.script_text
    assert plan.masked_sensitive_values["radius_secret"] == "***"


def test_vpn_radius_planner_does_not_emit_broken_peer_without_server_key():
    payload = dict(_payload())
    payload["server_public_key"] = ""
    plan = VpnRadiusBootstrapPlanner().plan(wizard_run_id=79, payload=payload)

    assert "/interface wireguard peers add" not in plan.script_text
    assert "HOBERADIUS_WG_SERVER_PUBKEY" in plan.script_text
    assert "no key set" in plan.script_text
    assert all(item["type"] != "interface.wireguard.peer" for item in plan.generated_objects)


def test_vpn_radius_script_has_no_forbidden_destructive_commands():
    plan = VpnRadiusBootstrapPlanner().plan(wizard_run_id=78, payload=_payload())
    low = plan.script_text.lower()
    for token in ("/remove", "\nremove ", "/interface disable", "reset-configuration", "system reset"):
        assert token not in low


def test_verification_contract_model_blocks_before_verified():
    svc = SetupVerificationService()
    contract = svc.build_contract(internet_verified=False, vpn_verified=False)
    status_map = contract["status_map"]
    assert status_map["vpn_tunnel"] == "blocked"
    assert status_map["hotspot_ready"] == "blocked"

    contract2 = svc.build_contract(internet_verified=True, vpn_verified=False)
    assert contract2["status_map"]["vpn_tunnel"] == "pending"
    assert contract2["status_map"]["hotspot_ready"] == "blocked"


def test_diagnostics_mapping_covers_required_codes():
    diagnostics = SetupDiagnosticsService()
    required = {
        "vpn_not_handshaking",
        "wrong_public_endpoint",
        "firewall_blocking_udp",
        "wrong_allowed_address",
        "route_missing",
        "radius_secret_mismatch",
        "radius_server_unreachable",
        "api_login_failed",
        "router_dns_issue",
        "router_time_issue",
        "duplicate_config_conflict",
        "management_interface_conflict",
    }
    available = set(diagnostics.list_all().keys())
    assert required.issubset(available)
    sample = diagnostics.get_diagnostic("vpn_not_handshaking")
    assert sample["arabic_title"]
    assert sample["commands_to_inspect"]


def test_blocked_transition_before_internet_verified(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        with pytest.raises(SetupWizardValidationError):
            svc.generate_vpn_radius_script(tenant_id=1, run_id=run["id"], payload=_payload())


def test_generate_vpn_script_after_internet_verified_and_hotspot_still_blocked(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        run_id = run["id"]
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        preview = svc.generate_vpn_radius_script(tenant_id=1, run_id=run_id, payload=_payload())
        assert "script_text" in preview
        step = svc.get_step(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_SCRIPT_PREVIEW)
        assert step and step["status"] == STEP_STATUS_GENERATED
        with pytest.raises(SetupWizardValidationError):
            svc.advance_to_step(tenant_id=1, run_id=run_id, step_key=STEP_HOTSPOT_CHOICE)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)
        svc.advance_to_step(tenant_id=1, run_id=run_id, step_key=STEP_HOTSPOT_CHOICE)
