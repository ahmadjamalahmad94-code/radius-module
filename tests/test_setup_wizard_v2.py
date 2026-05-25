from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-v2-" + secrets.token_hex(8)
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


def _create_run(client) -> int:
    res = _post(client, "/admin/radius/setup-wizard/runs", {})
    assert res.status_code == 200
    return int(res.get_json()["run"]["id"])


def test_setup_wizard_v2_route_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard-v2")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "data-setup-wizard-v2" in html
    assert "معالج إعداد HobeRadius" in html
    assert "setup_wizard_v2.css" in html
    assert "setup_wizard_v2.js" in html


def test_setup_wizard_v2_source_cards_render(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "DHCP Client" in html
    assert "PPPoE" in html
    assert "Static IP" in html
    assert "VLAN" in html
    assert 'data-source-type="dhcp"' in html
    assert 'data-source-type="pppoe"' in html


def test_setup_wizard_v2_stepper_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "الترحيب" in html
    assert "مصدر الإنترنت" in html
    assert "سكربت الإنترنت" in html
    assert "تحقق الإنترنت" in html
    assert "ربط VPN/RADIUS" in html


def test_setup_wizard_v2_preserves_engineering_link(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "فتح الوضع الهندسي" in html
    assert "/admin/radius/setup-wizard" in html


def test_setup_wizard_v2_verification_and_script_sections_exist(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-verify-output="internet"' in html
    assert 'data-swv2-verify-output="vpn"' in html
    assert 'data-swv2-script-preview="internet"' in html
    assert 'data-swv2-script-preview="vpn"' in html
    assert 'data-swv2-provisioning="router_vpn_ip"' in html
    assert "data-swv2-generate-vpn" in html
    assert "HOBERADIUS_SETUP:&lt;run_id&gt;:vpn" not in html
    assert "تم تجهيز سكربت الإنترنت" in html


def test_setup_wizard_v2_beginner_flow_hides_key_work_from_primary_ui(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "اسم الراوتر أو الموقع" in html
    assert "الباقي يجهزه المعالج تلقائيًا" in html
    assert "لا تحتاج لإدخال مفاتيح يدويًا" in html
    assert 'data-swv2-auto-public-key' in html
    assert "إدخال هندسي يدوي" in html
    assert "router provisioning reservation not found" not in html


def test_setup_wizard_v2_js_uses_real_preview_api(app):
    js = (
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "static",
            "js",
            "setup_wizard_v2.js",
        )
    )
    with open(js, "r", encoding="utf-8") as fh:
        source = fh.read()

    assert "/generate-internet-script" in source
    assert "/generate-vpn-radius-script" in source
    assert "/ip dhcp-client add interface=ether1" not in source
    assert "buildInternetPayload" in source
    assert "buildVpnPayload" in source


def test_setup_wizard_v2_js_extracts_public_key_and_accepts_partial_ping(app):
    js = (
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "static",
            "js",
            "setup_wizard_v2.js",
        )
    )
    with open(js, "r", encoding="utf-8") as fh:
        source = fh.read()

    assert "extractWireGuardPublicKey" in source
    assert "hasUsefulPing" in source
    assert "receivedMatch" in source
    assert "Number(receivedMatch[1]) > 0" in source
    assert "تعذر حفظ المفتاح" not in source


def test_v2_generate_internet_script_vlan_payload_returns_vlan_script(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _create_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
            {
                "source_type": "vlan",
                "selected_wan_interface": "ether1",
                "payload": {
                    "parent_interface": "ether1",
                    "vlan_id": 35,
                    "vlan_name": "wan-vlan35",
                    "address_mode": "dhcp",
                    "nat_enabled": True,
                    "add_default_route": True,
                    "use_peer_dns": True,
                },
            },
        )
        body = res.get_json()

    assert res.status_code == 200
    assert "/interface vlan add" in body["plan"]["script_text"]
    assert 'interface="ether1" vlan-id=35' in body["plan"]["script_text"]
    assert 'interface="wan-vlan35"' in body["plan"]["script_text"]


def test_v2_generate_internet_script_pppoe_payload_returns_pppoe_script(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _create_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
            {
                "source_type": "pppoe",
                "selected_wan_interface": "ether1",
                "payload": {
                    "interface": "ether1",
                    "username": "isp-user",
                    "password": "secret-pass",
                    "service_name": "ISP-PPPOE",
                    "nat_enabled": True,
                    "add_default_route": True,
                    "use_peer_dns": True,
                },
            },
        )
        body = res.get_json()

    assert res.status_code == 200
    assert "/interface pppoe-client add" in body["plan"]["script_text"]
    assert 'user="isp-user"' in body["plan"]["script_text"]
    assert 'password="secret-pass"' in body["plan"]["script_text"]
    assert body["plan"]["masked_sensitive_values"]["password"] == "***"


def test_v2_generate_internet_script_static_payload_returns_static_script(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _create_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
            {
                "source_type": "static",
                "selected_wan_interface": "ether1",
                "payload": {
                    "interface": "ether1",
                    "address_cidr": "192.0.2.2/24",
                    "gateway": "192.0.2.1",
                    "dns_servers": "1.1.1.1,8.8.8.8",
                    "nat_enabled": True,
                },
            },
        )
        body = res.get_json()

    assert res.status_code == 200
    assert "/ip address add" in body["plan"]["script_text"]
    assert "/ip route add" in body["plan"]["script_text"]
    assert 'gateway="192.0.2.1"' in body["plan"]["script_text"]


def test_v2_generate_internet_script_dhcp_payload_returns_dhcp_script(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _create_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
            {
                "source_type": "dhcp",
                "selected_wan_interface": "ether1",
                "payload": {
                    "interface": "ether1",
                    "add_default_route": True,
                    "use_peer_dns": True,
                    "nat_enabled": True,
                },
            },
        )
        body = res.get_json()

    assert res.status_code == 200
    assert "/ip dhcp-client add" in body["plan"]["script_text"]
    assert 'interface="ether1"' in body["plan"]["script_text"]


def test_existing_engineering_wizard_route_is_untouched(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "setup_wizard.js" in html
    assert "setup_wizard_v2.js" not in html
