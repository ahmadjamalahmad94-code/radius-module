from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.setup_wizard import STEP_INTERNET_VERIFICATION, get_setup_wizard_service
from app.radius.services.setup_wizard_provisioning_orchestrator import RouterProvisioningOrchestrator
from app.radius.services.setup_wizard_server_wg_readiness import MockCommandRunner
from app.radius.services.wireguard_peer_health import WireGuardPeerHealthService


VALID_KEY = "G" * 43 + "="
OTHER_KEY = "H" * 43 + "="


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wg-health-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_INTERFACE", "wg0")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
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


def _prepared_peer(app, public_key: str = VALID_KEY) -> tuple[int, dict]:
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        svc.mark_verified(tenant_id=1, run_id=run["id"], step_key=STEP_INTERNET_VERIFICATION)
        plan = svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "Health Router"},
        )
        registry_id = int(plan["router_provisioning"]["id"])
        result = RouterProvisioningOrchestrator().submit_router_public_key(
            tenant_id=1,
            registry_id=registry_id,
            public_key=public_key,
        )
        peer = dict(result["prepared_wireguard_peer"])
        peer["router_public_key"] = public_key
        return int(run["id"]), peer


def _wg_show(public_key: str, allowed_ip: str, *, handshake: str = "12 seconds ago", rx: str = "1.5 KiB", tx: str = "2.0 KiB") -> str:
    return (
        "interface: wg0\n"
        "  public key: SERVERKEY\n"
        "  listening port: 51820\n\n"
        f"peer: {public_key}\n"
        "  endpoint: 203.0.113.10:51820\n"
        f"  allowed ips: {allowed_ip}\n"
        f"  latest handshake: {handshake}\n"
        f"  transfer: {rx} received, {tx} sent\n"
        "  persistent keepalive: every 25 seconds\n"
    )


def test_healthy_peer_has_high_score(app):
    _, peer = _prepared_peer(app)
    result = WireGuardPeerHealthService().inspect_peer(
        prepared_peer=peer,
        wg_show_output=_wg_show(VALID_KEY, peer["allowed_ips"]),
    )

    assert result["status"] == "healthy"
    assert result["health_score"] == 92
    assert result["checks"]["handshake_recent"] is True
    assert result["peer"]["rx_bytes"] > 0
    assert "الربط نشط" == result["diagnostics"][0]["arabic_title"]


def test_applied_peer_without_handshake(app):
    _, peer = _prepared_peer(app)
    result = WireGuardPeerHealthService().inspect_peer(
        prepared_peer=peer,
        wg_show_output=_wg_show(VALID_KEY, peer["allowed_ips"], handshake="(none)", rx="0 B", tx="0 B"),
    )

    assert result["status"] == "applied_no_handshake"
    assert result["health_score"] == 55
    assert "الراوتر لم يتصل" in result["diagnostics"][0]["explanation_ar"]


def test_stale_peer_detected(app):
    _, peer = _prepared_peer(app)
    result = WireGuardPeerHealthService(stale_after_seconds=300).inspect_peer(
        prepared_peer=peer,
        wg_show_output=_wg_show(VALID_KEY, peer["allowed_ips"], handshake="15 minutes ago"),
    )

    assert result["status"] == "stale_peer"
    assert result["health_score"] == 45


def test_missing_peer_detected(app):
    _, peer = _prepared_peer(app)
    result = WireGuardPeerHealthService().inspect_peer(
        prepared_peer=peer,
        wg_show_output=_wg_show(OTHER_KEY, "10.10.0.99/32"),
    )

    assert result["status"] == "missing_peer"
    assert result["health_score"] == 0


def test_allowed_ip_mismatch_detected(app):
    _, peer = _prepared_peer(app)
    result = WireGuardPeerHealthService().inspect_peer(
        prepared_peer=peer,
        wg_show_output=_wg_show(VALID_KEY, "10.10.0.99/32"),
    )

    assert result["status"] == "allowed_ip_mismatch"
    assert result["health_score"] == 15
    assert "عنوان السماح" in result["diagnostics"][0]["arabic_title"]


def test_duplicate_peer_detected(app):
    _, peer = _prepared_peer(app)
    output = (
        _wg_show(VALID_KEY, peer["allowed_ips"])
        + "\n"
        + f"peer: {VALID_KEY}\n"
        + "  allowed ips: 10.10.0.99/32\n"
        + "  latest handshake: 10 seconds ago\n"
    )
    result = WireGuardPeerHealthService().inspect_peer(prepared_peer=peer, wg_show_output=output)

    assert result["status"] == "duplicate_peer"
    assert result["health_score"] == 10


def test_offline_peer_when_transfer_frozen(app):
    _, peer = _prepared_peer(app)
    previous = {"peer": {"rx_bytes": 1024, "tx_bytes": 2048}}
    result = WireGuardPeerHealthService(offline_after_seconds=900).inspect_peer(
        prepared_peer=peer,
        wg_show_output=_wg_show(VALID_KEY, peer["allowed_ips"], handshake="20 minutes ago", rx="1024", tx="2048"),
        previous_observation=previous,
    )

    assert result["status"] == "offline"
    assert result["health_score"] == 30


def test_read_only_runner_path_does_not_mutate(app):
    _, peer = _prepared_peer(app)
    runner = MockCommandRunner({"wg show wg0": _wg_show(VALID_KEY, peer["allowed_ips"])})
    result = WireGuardPeerHealthService(runner=runner, interface="wg0").inspect_peer(prepared_peer=peer)

    assert result["status"] == "healthy"
    assert runner.commands == ["wg show wg0"]


def test_health_endpoint_returns_structured_json(app):
    run_id, peer = _prepared_peer(app)
    with app.test_client() as client:
        _auth_session(client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/server-peer/health",
            {"output": _wg_show(VALID_KEY, peer["allowed_ips"])},
        )
        data = res.get_json()

    assert res.status_code == 200
    assert data["ok"] is True
    assert data["health"]["status"] == "healthy"


def test_v2_route_renders_peer_health_panel(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "data-swv2-peer-health-panel" in html
    assert "data-swv2-server-peer-health" in html
