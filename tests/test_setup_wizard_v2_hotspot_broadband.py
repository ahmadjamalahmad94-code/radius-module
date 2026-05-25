from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_VERIFICATION,
    get_setup_wizard_service,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-v2-hsbb-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa_admin"
        sess["admin_name"] = "QA Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def _post(client, url: str, payload: dict):
    return client.post(url, json=payload, headers={"X-CSRFToken": "test-csrf"})


def _verified_run(app, client) -> int:
    res = _post(client, "/admin/radius/setup-wizard/runs", {})
    assert res.status_code == 200
    run_id = int(res.get_json()["run"]["id"])
    with app.app_context():
        svc = get_setup_wizard_service()
        svc.set_internet_source(
            tenant_id=1,
            run_id=run_id,
            source_type="dhcp",
            selected_wan_interface="ether1",
            input_json={"interface": "ether1"},
        )
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)
    return run_id


def test_v2_route_renders_service_choice_step(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-step="service-path"' in html
    assert 'data-service-path="hotspot"' in html
    assert 'data-service-path="broadband"' in html
    assert 'data-service-path="both"' in html
    assert 'data-service-path="skip"' in html


def test_hotspot_and_broadband_options_locked_before_vpn_success(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-locked-until="vpn-radius"' in html
    assert 'data-swv2-service-lock' in html


def test_interface_picker_markup_and_manual_override_exist(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-interface-picker' in html
    assert 'data-swv2-load-interfaces' in html
    assert 'data-swv2-manual-interfaces' in html
    assert 'data-swv2-interface-summary' in html
    assert 'data-interface-name="ether8"' in html
    assert '<details class="swv2-advanced">' in html


def test_backend_route_with_hotspot_payload_returns_script(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-hotspot-script",
            {
                "mode": "manual",
                "payload": {
                    "selected_interfaces": ["ether3"],
                    "network_cidr": "10.77.50.0/24",
                    "pool_range": "10.77.50.20-10.77.50.220",
                    "gateway_ip": "10.77.50.1",
                    "bridge_name": "hs-bridge",
                    "profile_name": "hs-profile",
                    "server_name": "hs-server",
                },
                "blocked_network_cidrs": ["10.10.0.0/24"],
            },
        )
        data = res.get_json()

    assert res.status_code == 200
    assert "/ip hotspot add" in data["plan"]["script_text"]


def test_backend_route_with_broadband_payload_returns_script(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-broadband-script",
            {
                "mode": "manual",
                "payload": {
                    "selected_interfaces": ["ether3"],
                    "service_name": "hoberadius-pppoe",
                    "local_address": "10.88.44.1",
                    "remote_pool_cidr": "10.88.44.0/24",
                    "profile_name": "hr-pppoe-profile",
                },
                "blocked_network_cidrs": ["10.10.0.0/24"],
            },
        )
        data = res.get_json()

    assert res.status_code == 200
    assert "/interface pppoe-server server" in data["plan"]["script_text"]
    assert 'src-address="10.88.44.0/24"' in data["plan"]["script_text"]


def test_v2_js_uses_real_service_apis_without_mock_scripts(app):
    js_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "static",
        "js",
        "setup_wizard_v2.js",
    )
    with open(js_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    assert "generate-${service}-script" in source
    assert "dry-run/${service}" in source
    assert "verify-${service}" in source
    assert "/ip hotspot add" not in source
    assert "/interface pppoe-server server" not in source


def test_dry_run_buttons_present_and_no_service_apply_button(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-service-dry-run="hotspot"' in html
    assert 'data-swv2-service-dry-run="broadband"' in html
    assert "data-swv2-service-apply" not in html
    assert "data-swv2-server-peer-apply" in html


def test_engineering_view_route_remains_separate(app):
    with app.test_client() as client:
        _auth_session(client)
        html_v2 = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)
        engineering = client.get("/admin/radius/setup-wizard")

    assert "/admin/radius/setup-wizard" in html_v2
    assert engineering.status_code == 200
