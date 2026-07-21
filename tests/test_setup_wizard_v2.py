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
    # حارس دورة حياة الترخيص يقفل اللوحة على قاعدة جديدة بلا لقطة
    # ترخيص؛ تجاوزه في الاختبارات يحتاج العلمين معًا (راجع
    # license_lifecycle._test_bypass_active وتعليق tests/conftest.py).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa_admin"
        sess["admin_name"] = "QA Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"
        # «الإعداد الهندسي» super_admin فقط (مخفي مؤقتاً بطلب المالك)
        sess["is_super_admin"] = True


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

    assert "أخذ عنوان تلقائي" in html
    assert "اتصال مزود الإنترنت" in html
    assert "عنوان ثابت" in html
    assert "شبكة افتراضية" in html
    assert 'data-source-type="dhcp"' in html
    assert 'data-source-type="pppoe"' in html


def test_setup_wizard_v2_stepper_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-step-target="welcome"' in html
    assert 'data-swv2-step-target="source"' in html
    assert 'data-swv2-step-target="internet-script"' in html
    assert 'data-swv2-step-target="internet-verify"' in html
    assert 'data-swv2-step-target="vpn-script"' in html


def test_setup_wizard_v2_hides_engineering_link_from_simple_flow(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "فتح الوضع الهندسي" not in html


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
    assert 'data-copy-target="internet-script-code"' in html
    assert 'data-copy-target="vpn-script-code"' in html
    assert 'data-copy-target="hotspot-script-code"' in html
    assert 'data-copy-target="broadband-script-code"' in html
    assert 'data-copy-target="added-service-plan-code"' in html
    # The legacy «paste WG output → dry-run → apply» card was
    # replaced by the one-button auto-finalize card. The new
    # contract: a single button + four input fields + a result
    # block. See the «server_peer_complete_setup» service
    # method.
    assert "data-swv2-auto-finalize" in html
    assert "data-swv2-auto-finalize-go" in html
    assert "data-swv2-auto-router-address" in html
    assert "data-swv2-auto-api-user" in html
    assert "data-swv2-auto-api-password" in html
    assert "تجهيز كامل تلقائياً" in html
    assert "Terminal السيرفر VPS" not in html
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
    assert "إدخال هندسي يدوي" not in html
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
    assert "public-key" in source
    assert "private-key=" in source
    assert "endpoint-address=" in source
    assert "name=hr-wg" in source
    assert "publicKeyFromLine" in source
    assert "isPeerLine" in source
    assert "matches.find" in source
    assert "hasUsefulPing" in source
    assert "hasHandshakeSuccess" in source
    assert 'confirmationOverride || serverPeerConfirmation()' in source
    assert "receivedMatch" in source
    assert "Number(receivedMatch[1]) > 0" in source
    assert 'kind !== "vpn" || hasPingSuccess || hasHandshakeSuccess' in source
    assert "ØªØ¹Ø°Ø± Ø­ÙØ¸ Ø§Ù„Ù…ÙØªØ§Ø­" not in source


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


def test_v2_js_treats_successful_vpn_output_as_no_extra_vps_step_needed():
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

    assert "markServerPeerAlreadyConnected" in source
    assert "تم تأكيد الربط عبر ping/handshake" in source
    assert "server-peer/dry-run" in source
    assert "APPLY SERVER PEER IN LAB" in source
    assert "تم تجهيز خطة الربط على الخادم داخل HobeRadius" in source
    assert "JSON.stringify(value || {}, null, 2)" not in source
