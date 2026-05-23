from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_INTERNET_SCRIPT_PREVIEW,
    STEP_STATUS_GENERATED,
    SetupWizardValidationError,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_internet_planner import InternetUplinkScriptPlanner


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-net-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


def _planner() -> InternetUplinkScriptPlanner:
    return InternetUplinkScriptPlanner()


def _forbidden_absent(script: str) -> None:
    low = script.lower()
    assert "/remove" not in low
    assert "\nremove " not in low
    assert "reset-configuration" not in low
    assert "/interface disable" not in low


def test_vlan_dhcp_script_generation():
    plan = _planner().plan(
        wizard_run_id=41,
        source_type="vlan",
        payload={
            "parent_interface": "ether1",
            "vlan_id": 35,
            "vlan_name": "wan-vlan35",
            "address_mode": "dhcp",
            "nat_enabled": True,
            "add_default_route": True,
            "use_peer_dns": True,
        },
    )
    assert "HOBERADIUS_SETUP:41:internet" in plan.script_text
    assert "/tool ping 8.8.8.8 count=5" in plan.script_text
    assert "out-interface=\"wan-vlan35\"" in plan.script_text
    _forbidden_absent(plan.script_text)


def test_vlan_static_script_generation():
    plan = _planner().plan(
        wizard_run_id=42,
        source_type="vlan",
        payload={
            "parent_interface": "ether1",
            "vlan_id": 120,
            "vlan_name": "uplink-v120",
            "address_mode": "static",
            "address_cidr": "10.77.3.2/24",
            "gateway": "10.77.3.1",
            "dns_servers": ["1.1.1.1", "8.8.8.8"],
            "nat_enabled": False,
        },
    )
    assert "/ip address add interface=\"uplink-v120\" address=\"10.77.3.2/24\"" in plan.script_text
    assert "/ip route add dst-address=0.0.0.0/0 gateway=\"10.77.3.1\"" in plan.script_text
    assert "/tool ping cloudflare.com count=5" in plan.script_text
    _forbidden_absent(plan.script_text)


def test_static_ip_script_generation():
    plan = _planner().plan(
        wizard_run_id=43,
        source_type="static",
        payload={
            "interface": "ether1",
            "address_cidr": "192.0.2.2/24",
            "gateway": "192.0.2.1",
            "dns_servers": "9.9.9.9,1.1.1.1",
            "nat_enabled": True,
        },
    )
    assert "out-interface=\"ether1\"" in plan.script_text
    assert "/ip dns set servers=\"9.9.9.9,1.1.1.1\"" in plan.script_text
    _forbidden_absent(plan.script_text)


def test_direct_dhcp_script_generation():
    plan = _planner().plan(
        wizard_run_id=44,
        source_type="dhcp",
        payload={
            "interface": "ether1",
            "add_default_route": False,
            "use_peer_dns": False,
            "nat_enabled": True,
        },
    )
    assert "add-default-route=no" in plan.script_text
    assert "use-peer-dns=no" in plan.script_text
    assert "out-interface=\"ether1\"" in plan.script_text
    _forbidden_absent(plan.script_text)


def test_pppoe_script_generation_and_masking():
    plan = _planner().plan(
        wizard_run_id=45,
        source_type="pppoe",
        payload={
            "interface": "ether1",
            "username": "isp-user",
            "password": "SuperSecret123!",
            "service_name": "ISP-PPPOE",
            "nat_enabled": True,
        },
    )
    assert "password=\"SuperSecret123!\"" in plan.script_text
    assert plan.masked_sensitive_values.get("password") == "***"
    assert plan.input_safe.get("username") == "isp-user"
    assert plan.input_safe.get("service_name") == "ISP-PPPOE"
    assert "password" not in plan.input_safe
    _forbidden_absent(plan.script_text)


def test_invalid_vlan_id_rejected():
    with pytest.raises(SetupWizardValidationError):
        _planner().plan(
            wizard_run_id=50,
            source_type="vlan",
            payload={
                "parent_interface": "ether1",
                "vlan_id": 5000,
                "address_mode": "dhcp",
            },
        )


def test_invalid_cidr_rejected():
    with pytest.raises(SetupWizardValidationError):
        _planner().plan(
            wizard_run_id=51,
            source_type="static",
            payload={
                "interface": "ether1",
                "address_cidr": "10.0.0.999/24",
                "gateway": "10.0.0.1",
            },
        )


def test_missing_pppoe_password_rejected():
    with pytest.raises(SetupWizardValidationError):
        _planner().plan(
            wizard_run_id=52,
            source_type="pppoe",
            payload={"interface": "ether1", "username": "u1"},
        )


def test_nat_targets_only_selected_or_generated_uplink():
    vlan_plan = _planner().plan(
        wizard_run_id=53,
        source_type="vlan",
        payload={
            "parent_interface": "ether1",
            "vlan_id": 10,
            "vlan_name": "v10",
            "address_mode": "dhcp",
            "nat_enabled": True,
        },
    )
    ppp_plan = _planner().plan(
        wizard_run_id=54,
        source_type="pppoe",
        payload={
            "interface": "ether1",
            "username": "x",
            "password": "y",
            "nat_enabled": True,
            "pppoe_client_name": "hr-pppoe-test",
        },
    )
    assert "out-interface=\"v10\"" in vlan_plan.script_text
    assert "out-interface=\"hr-pppoe-test\"" in ppp_plan.script_text
    assert "out-interface-list" not in vlan_plan.script_text
    assert "out-interface-list" not in ppp_plan.script_text


def test_wizard_state_marks_internet_step_generated(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        plan = svc.generate_internet_script(
            tenant_id=1,
            run_id=run["id"],
            source_type="dhcp",
            selected_wan_interface="ether1",
            payload={
                "interface": "ether1",
                "add_default_route": True,
                "use_peer_dns": True,
                "nat_enabled": True,
            },
        )
        assert "script_text" in plan
        step = svc.get_step(
            tenant_id=1, run_id=run["id"], step_key=STEP_INTERNET_SCRIPT_PREVIEW
        )
        assert step is not None
        assert step["status"] == STEP_STATUS_GENERATED
        assert "/tool ping 8.8.8.8 count=5" in step["generated_script"]


def test_no_mikrotik_execution_adapter_introduced():
    plan = _planner().plan(
        wizard_run_id=55,
        source_type="dhcp",
        payload={"interface": "ether1"},
    )
    # Planner is pure text generation output, no execution metadata.
    payload = plan.to_dict()
    assert "execute" not in payload
    assert "applied" not in payload
