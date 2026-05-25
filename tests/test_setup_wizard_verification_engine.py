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
    VpsNetworkProbe,
)
from app.radius.services.setup_wizard_server_wg_readiness import CommandSafetyClassifier


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


def _vpn_router_only_output() -> str:
    return "\n".join(
        [
            "latest handshake: 7s ago",
            "/tool ping 10.10.0.1 count=5 sent=5 received=5 packet-loss=0%",
            "/radius print detail address=10.10.0.1 service=hotspot,ppp",
            "/user print detail name=hr_api_setup",
        ]
    )


def _vpn_router_ping_success_output() -> str:
    return "\n".join(
        [
            '/tool ping "10.10.0.1" count=5',
            "sent=5 received=5 packet-loss=0% min-rtt=61ms310us avg-rtt=61ms917us max-rtt=62ms653us",
            '/radius print detail where comment~"HOBERADIUS_SETUP:10:radius"',
        ]
    )


class _OkVpsProbeAdapter:
    def ping_router_vpn_ip(self, ip: str, *, timeout_seconds: float = 2.0) -> dict:
        return {"ok": True, "target": ip, "stdout": "3 packets transmitted, 3 received, 0% packet loss"}

    def inspect_wireguard_peer(self, peer_identifier: str, *, timeout_seconds: float = 2.0) -> dict:
        return {"ok": False}

    def check_udp_port_hint(self, host: str, port: int, *, timeout_seconds: float = 2.0) -> dict:
        return {"ok": False}


class _PeerVpsProbeAdapter:
    def __init__(self, *, allowed_ips: str, latest_handshake: str = "12 seconds ago") -> None:
        self.allowed_ips = allowed_ips
        self.latest_handshake = latest_handshake

    def ping_router_vpn_ip(self, ip: str, *, timeout_seconds: float = 2.0) -> dict:
        return {"ok": True, "target": ip, "stdout": "3 packets transmitted, 3 received, 0% packet loss"}

    def inspect_wireguard_peer(self, peer_identifier: str, *, timeout_seconds: float = 2.0) -> dict:
        return {
            "ok": True,
            "public_key": peer_identifier,
            "allowed_ips": self.allowed_ips,
            "latest_handshake": self.latest_handshake,
        }

    def check_udp_port_hint(self, host: str, port: int, *, timeout_seconds: float = 2.0) -> dict:
        return {"ok": False}


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


def test_vpn_radius_pasted_output_can_confirm_vps_ping_from_server_probe():
    svc = SetupVerificationService(vps_probe=VpsNetworkProbe(_OkVpsProbeAdapter()))
    result = svc.verify_vpn_radius(
        run={},
        vpn_payload={"router_vpn_ip": "10.10.0.7", "vps_vpn_ip": "10.10.0.1", "radius_server_ip": "10.10.0.1"},
        mode="pasted_output",
        payload={"output": _vpn_router_only_output()},
    ).to_dict()

    assert result["gate_unlocked"] is True
    assert result["overall_status"] == "success"
    assert result["raw_observations"]["vps_ping_router_probe"]["target"] == "10.10.0.7"


def test_vpn_radius_router_ping_from_mikrotik_unlocks_without_server_back_ping():
    svc = SetupVerificationService()
    result = svc.verify_vpn_radius(
        run={},
        vpn_payload={"router_vpn_ip": "10.10.0.7", "vps_vpn_ip": "10.10.0.1", "radius_server_ip": "10.10.0.1"},
        mode="pasted_output",
        payload={"output": _vpn_router_ping_success_output()},
    ).to_dict()

    by_key = {item["key"]: item["status"] for item in result["checks"]}
    assert result["gate_unlocked"] is True
    assert result["overall_status"] == "success"
    assert by_key["router_ping_vps"] == "success"
    assert result["raw_observations"]["vpn_evidence"] == "router_ping_vps"


def test_vpn_radius_fails_when_server_allowed_ips_do_not_match_router_reservation():
    public_key = "E" * 43 + "="
    svc = SetupVerificationService(
        vps_probe=VpsNetworkProbe(_PeerVpsProbeAdapter(allowed_ips="10.10.0.13/32"))
    )
    result = svc.verify_vpn_radius(
        run={},
        vpn_payload={
            "router_vpn_ip": "10.10.0.14",
            "vps_vpn_ip": "10.10.0.1",
            "radius_server_ip": "10.10.0.1",
            "router_public_key": public_key,
            "expected_allowed_ips": "10.10.0.14/32",
        },
        mode="pasted_output",
        payload={"output": _vpn_router_ping_success_output()},
    ).to_dict()

    by_key = {item["key"]: item["status"] for item in result["checks"]}
    assert result["gate_unlocked"] is False
    assert result["overall_status"] == "failed"
    assert by_key["server_allowed_ips_consistency"] == "failed"
    assert any(item.get("code") == "server_allowed_ip_mismatch" for item in result["diagnostics"])
    assert "عنوان الراوتر على الخادم غير مطابق" in result["next_action_ar"]


def test_vpn_radius_passes_after_server_allowed_ips_match_router_reservation():
    public_key = "E" * 43 + "="
    svc = SetupVerificationService(
        vps_probe=VpsNetworkProbe(_PeerVpsProbeAdapter(allowed_ips="10.10.0.14/32"))
    )
    result = svc.verify_vpn_radius(
        run={},
        vpn_payload={
            "router_vpn_ip": "10.10.0.14",
            "vps_vpn_ip": "10.10.0.1",
            "radius_server_ip": "10.10.0.1",
            "router_public_key": public_key,
            "expected_allowed_ips": "10.10.0.14/32",
        },
        mode="pasted_output",
        payload={"output": _vpn_router_ping_success_output()},
    ).to_dict()

    by_key = {item["key"]: item["status"] for item in result["checks"]}
    assert result["gate_unlocked"] is True
    assert result["overall_status"] == "success"
    assert by_key["server_allowed_ips_consistency"] == "success"


def test_server_ping_command_is_read_only_but_writes_remain_blocked():
    classifier = CommandSafetyClassifier()

    assert classifier.classify("ping -c 3 10.10.0.7").allowed_read_only is True
    assert classifier.classify("wg set wg0 peer abc allowed-ips 10.10.0.7/32").allowed_read_only is False


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
