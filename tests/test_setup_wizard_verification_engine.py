from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard_verification import (
    ProbeUnavailableError,
    ReadOnlyCommandRejected,
    RouterReadOnlyProbe,
    SetupDiagnosticsService,
    SetupVerificationService,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-verify-" + secrets.token_hex(8)
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
    return client.post(
        url,
        json=payload,
        headers={"X-CSRFToken": "test-csrf"},
    )


def _internet_ok_output() -> str:
    return "sent=5 received=5 packet-loss=0%"


def _vpn_ok_output() -> str:
    return "\n".join(
        [
            "latest handshake: 7s ago",
            "/tool ping 10.10.0.1 count=5 sent=5 received=5 packet-loss=0%",
            "vps_ping_router=ok",
            "/radius print detail address=10.10.0.1 service=hotspot,ppp",
            "/user print detail name=hr_api_setup",
        ]
    )


def _build_run(client) -> int:
    run = _post(client, "/admin/radius/setup-wizard/runs", {}).get_json()["run"]
    run_id = int(run["id"])
    _post(
        client,
        f"/admin/radius/setup-wizard/runs/{run_id}/internet-source",
        {"source_type": "dhcp", "selected_wan_interface": "ether1", "input_json": {"interface": "ether1", "nat_enabled": True}},
    )
    return run_id


def test_internet_pasted_ping_success_marks_verified(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _build_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": _internet_ok_output()},
        )
        body = res.get_json()
        assert res.status_code == 200
        assert body["status"] == "success"
        assert body["gate_unlocked"] is True


def test_internet_pasted_ping_failure_keeps_gate_blocked(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _build_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": "sent=5 received=0 packet-loss=100%"},
        )
        body = res.get_json()
        assert res.status_code == 200
        assert body["gate_unlocked"] is False
        assert body["status"] in {"failed", "partial"}


def test_vpn_radius_missing_required_checks_blocks_gate(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _build_run(client)
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": _internet_ok_output()},
        )
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script",
            {"payload": {"router_vpn_ip": "10.10.0.3", "vps_vpn_ip": "10.10.0.1"}},
        )
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-vpn-radius",
            {"mode": "pasted_output", "output": "no handshake"},
        )
        body = res.get_json()
        assert body["gate_unlocked"] is False
        assert any(item.get("code") == "vpn_not_handshaking" for item in body["diagnostics"])


def test_vpn_radius_required_checks_unlock_gate(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _build_run(client)
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": _internet_ok_output()},
        )
        _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script",
            {"payload": {"router_vpn_ip": "10.10.0.3", "vps_vpn_ip": "10.10.0.1"}},
        )
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-vpn-radius",
            {"mode": "pasted_output", "output": _vpn_ok_output()},
        )
        body = res.get_json()
        assert body["status"] == "success"
        assert body["gate_unlocked"] is True


def test_hotspot_verification_success_marks_verified(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _build_run(client)
        _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet", {"mode": "pasted_output", "output": _internet_ok_output()})
        _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script", {"payload": {"router_vpn_ip": "10.10.0.3", "vps_vpn_ip": "10.10.0.1"}})
        _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/verify-vpn-radius", {"mode": "pasted_output", "output": _vpn_ok_output()})
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-hotspot",
            {"mode": "manual_contract", "checks": {"hotspot_server_present": True, "radius_enabled": True, "hotspot_pool_present": True, "hotspot_nat_present": True}},
        )
        body = res.get_json()
        assert body["gate_unlocked"] is True
        assert body["status"] == "success"


def test_broadband_verification_success_marks_verified(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _build_run(client)
        _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet", {"mode": "pasted_output", "output": _internet_ok_output()})
        _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/generate-vpn-radius-script", {"payload": {"router_vpn_ip": "10.10.0.3", "vps_vpn_ip": "10.10.0.1"}})
        _post(client, f"/admin/radius/setup-wizard/runs/{run_id}/verify-vpn-radius", {"mode": "pasted_output", "output": _vpn_ok_output()})
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-broadband",
            {"mode": "manual_contract", "checks": {"pppoe_service_present": True, "radius_enabled": True, "broadband_nat_present": True, "ppp_profile_present": True, "remote_pool_present": True}},
        )
        body = res.get_json()
        assert body["gate_unlocked"] is True
        assert body["status"] == "success"


def test_read_only_command_guard_rejects_write_commands():
    probe = RouterReadOnlyProbe()
    with pytest.raises(ReadOnlyCommandRejected):
        probe.run_read_only_command("/ip route add dst-address=0.0.0.0/0 gateway=1.1.1.1")


def test_probe_unavailable_returns_blocked_not_exception():
    svc = SetupVerificationService()
    result = svc.verify_internet(
        run={"selected_wan_interface": "ether1", "internet_source_type": "dhcp"},
        internet_input={"interface": "ether1", "nat_enabled": True, "add_default_route": True},
        mode="probe",
        payload={},
    ).to_dict()
    assert result["overall_status"] == "blocked"
    assert result["gate_unlocked"] is False
    assert any(item.get("code") == "probe_unavailable" for item in result["diagnostics"])


def test_diagnostics_include_arabic_title_and_explanation():
    payload = SetupDiagnosticsService().get_diagnostic("api_login_failed")
    assert payload["arabic_title"]
    assert payload["explanation_ar"]


def test_secrets_not_leaked_in_raw_observations():
    svc = SetupVerificationService()
    result = svc.verify_vpn_radius(
        run={},
        vpn_payload={"radius_secret": "TopSecret", "api_username": "x"},
        mode="manual_contract",
        payload={"checks": {"vpn_tunnel": True, "router_ping_vps": True, "vps_ping_router": True, "radius_reachable": True, "api_login": True}},
    ).to_dict()
    assert "TopSecret" not in str(result["raw_observations"])


def test_route_verification_returns_structured_json_and_ui_has_output_areas(app):
    with app.test_client() as client:
        _auth_session(client)
        page = client.get("/admin/radius/setup-wizard")
        text = page.get_data(as_text=True)
        assert 'name="verify_output"' in text

        run_id = _build_run(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/verify-internet",
            {"mode": "pasted_output", "output": _internet_ok_output()},
        )
        body = res.get_json()
        assert "checks" in body
        assert "diagnostics" in body
        assert "gate_unlocked" in body

