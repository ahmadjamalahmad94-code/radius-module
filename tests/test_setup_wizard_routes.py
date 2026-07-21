from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-routes-" + secrets.token_hex(8)
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

    created = create_app()
    return created


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
    return client.post(
        url,
        json=payload,
        headers={"X-CSRFToken": "test-csrf"},
    )


def _ok_ping_output() -> str:
    return "sent=5 received=5 packet-loss=0%"


def _ok_vpn_output() -> str:
    return "\n".join(
        [
            "latest handshake: 3s ago",
            "/tool ping 10.10.0.1 count=5 sent=5 received=5 packet-loss=0%",
            "vps_ping_router=ok",
            "/radius print detail address=10.10.0.1 service=hotspot,ppp",
            "/user print detail name=hr_api_setup group=api",
        ]
    )


def test_setup_wizard_page_loads(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        assert res.status_code == 200
        assert "setup_wizard.js" in res.get_data(as_text=True)


def test_create_and_read_run_summary(app):
    with app.test_client() as client:
        _auth_session(client)
        create = _post(client, "/admin/radius/setup-wizard/runs", {})
        assert create.status_code == 200
        run_id = create.get_json()["run"]["id"]
        summary = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/summary")
        body = summary.get_json()
        assert summary.status_code == 200
        assert body["ok"] is True
        assert body["run"]["id"] == run_id


def test_vpn_generation_blocked_before_internet_verified(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script",
            {"payload": {"router_vpn_ip": "10.10.0.3"}},
        )
        body = res.get_json()
        assert res.status_code == 400
        assert body["ok"] is False
        assert "internet verification is required first" in body["error"]


def test_hotspot_and_broadband_blocked_until_vpn_verified(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/internet-source",
            {"source_type": "dhcp", "selected_wan_interface": "ether1", "input_json": {"interface": "ether1"}},
        )
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": _ok_ping_output()},
        )

        hs = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-hotspot-script",
            {"mode": "smart", "payload": {"selected_interfaces": ["ether3"]}, "blocked_network_cidrs": ["10.10.0.0/24"]},
        )
        bb = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-broadband-script",
            {"mode": "smart", "payload": {"selected_interfaces": ["ether4"]}, "blocked_network_cidrs": ["10.10.0.0/24"]},
        )
        assert hs.status_code == 400
        assert bb.status_code == 400


def test_generation_flow_persists_step_states(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]["id"]

        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-internet-script",
            {
                "source_type": "dhcp",
                "selected_wan_interface": "ether1",
                "payload": {"interface": "ether1", "add_default_route": True, "use_peer_dns": True, "nat_enabled": True},
            },
        )
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": _ok_ping_output()},
        )

        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script",
            {
                "payload": {
                    "wg_interface_name": "hr-wg",
                    "peer_name": "vps-peer",
                    "router_vpn_ip": "10.10.0.3",
                    "vps_vpn_ip": "10.10.0.1",
                    "allowed_address": "10.10.0.1/32",
                    "vps_public_endpoint": "187.77.70.18",
                    "radius_server_ip": "10.10.0.1",
                    "radius_secret": "XyZ123!",
                    "api_username": "hr_api_setup",
                }
            },
        )
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-vpn-radius",
            {"mode": "pasted_output", "output": _ok_vpn_output()},
        )
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-hotspot-script",
            {"mode": "smart", "payload": {"selected_interfaces": ["ether3"]}, "blocked_network_cidrs": ["10.10.0.0/24"]},
        )
        summary = client.get(f"/admin/radius/setup-wizard/runs/{run_id}/summary").get_json()
        step_keys = {step["step_key"]: step["status"] for step in summary["steps"]}
        assert step_keys["internet_script_preview"] == "generated"
        assert step_keys["internet_verification"] == "verified"
        assert step_keys["vpn_radius_script_preview"] == "generated"
        assert step_keys["vpn_radius_verification"] == "verified"
        assert step_keys["hotspot_script_preview"] == "generated"
