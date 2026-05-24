from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_common import SetupWizardValidationError
from app.radius.services.setup_wizard_provisioning_orchestrator import (
    RouterProvisioningOrchestrator,
)
from app.radius.services.setup_wizard_server_wg import (
    MockServerWireGuardWriteAdapter,
    ServerWireGuardInspector,
    ServerWireGuardPeerApplyService,
    ServerWireGuardPeerPlanner,
    ServerWireGuardSafetyValidator,
    server_peer_confirmation_phrase,
    server_peer_rollback_phrase,
)


VALID_KEY_1 = "C" * 43 + "="
VALID_KEY_2 = "D" * 43 + "="


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "server-wg-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", raising=False)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
    from app import create_app

    return create_app()


def _prepared_peer(app, public_key: str = VALID_KEY_1) -> dict:
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        svc.mark_verified(
            tenant_id=1,
            run_id=run["id"],
            step_key=STEP_INTERNET_VERIFICATION,
        )
        plan = svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "Branch"},
        )
        registry_id = int(plan["router_provisioning"]["id"])
        result = RouterProvisioningOrchestrator().submit_router_public_key(
            tenant_id=1,
            registry_id=registry_id,
            public_key=public_key,
        )
        return {
            "run_id": run["id"],
            "registry_id": registry_id,
            "prepared_peer_id": int(result["prepared_wireguard_peer"]["id"]),
            "router_vpn_ip": result["prepared_wireguard_peer"]["router_vpn_ip"],
        }


def _wg_show(public_key: str = VALID_KEY_1, allowed_ip: str = "10.10.0.2/32") -> str:
    return (
        f"peer: {public_key}\n"
        "  endpoint: 198.51.100.10:51820\n"
        f"  allowed ips: {allowed_ip}\n"
        "  latest handshake: 12 seconds ago\n"
        "  transfer: 1.2 KiB received, 2.4 KiB sent\n"
    )


def test_server_peer_dry_run_creates_preview(app):
    peer = _prepared_peer(app)
    with app.app_context():
        result = ServerWireGuardPeerApplyService().dry_run(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
        )

    assert result["status"] == "ready"
    assert "wg set wg0 peer" in result["plan"]["command_preview"]
    assert "HOBERADIUS_SETUP" in result["plan"]["rollback_preview"]


def test_server_peer_apply_blocked_by_default_flags(app):
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService()
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert result["code"] == "server_wg_apply_feature_flags_disabled"


def test_server_peer_apply_blocked_without_public_key(app):
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        svc.mark_verified(
            tenant_id=1,
            run_id=run["id"],
            step_key=STEP_INTERNET_VERIFICATION,
        )
        plan = svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "No Key"},
        )
        peer_id = int(plan["prepared_wireguard_peer"]["id"])
        with pytest.raises(SetupWizardValidationError):
            ServerWireGuardPeerApplyService().dry_run(tenant_id=1, prepared_peer_id=peer_id)


def test_duplicate_public_key_blocked_by_inspector(app):
    peer = _prepared_peer(app)
    planner = ServerWireGuardPeerPlanner(
        inspector=ServerWireGuardInspector(wg_show_output=_wg_show(VALID_KEY_1, "10.10.0.99/32"))
    )
    with app.app_context():
        with pytest.raises(SetupWizardValidationError):
            ServerWireGuardPeerApplyService(planner=planner).dry_run(
                tenant_id=1,
                prepared_peer_id=peer["prepared_peer_id"],
            )


def test_duplicate_allowed_ip_blocked_by_inspector(app):
    peer = _prepared_peer(app)
    planner = ServerWireGuardPeerPlanner(
        inspector=ServerWireGuardInspector(wg_show_output=_wg_show(VALID_KEY_2, peer["router_vpn_ip"] + "/32"))
    )
    with app.app_context():
        with pytest.raises(SetupWizardValidationError):
            ServerWireGuardPeerApplyService(planner=planner).dry_run(
                tenant_id=1,
                prepared_peer_id=peer["prepared_peer_id"],
            )


def test_rollback_requires_exact_generated_tag(app):
    with pytest.raises(SetupWizardValidationError):
        ServerWireGuardSafetyValidator().validate_rollback(
            rollback_preview="wg set wg0 peer CCCCC remove",
            tag="HOBERADIUS_ROUTER:1 HOBERADIUS_SETUP:1:server-peer",
        )


def test_verify_parses_wg_show_output_and_marks_vpn_verified(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", "true")
    peer = _prepared_peer(app)
    adapter = MockServerWireGuardWriteAdapter()
    with app.app_context():
        service = ServerWireGuardPeerApplyService(write_adapter=adapter)
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        applied = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )
        assert applied["status"] == "applied"
        verified = service.verify(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            wg_show_output=_wg_show(VALID_KEY_1, peer["router_vpn_ip"] + "/32"),
        )
        lifecycle = get_setup_wizard_service().get_run_summary(
            tenant_id=1,
            run_id=peer["run_id"],
        )["router_lifecycle"]

    assert verified["status"] == "success"
    assert lifecycle["current_state"] == "vpn_verified"


def test_lifecycle_does_not_skip_to_vpn_verified_on_apply(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", "true")
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService(write_adapter=MockServerWireGuardWriteAdapter())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )
        lifecycle = get_setup_wizard_service().get_run_summary(
            tenant_id=1,
            run_id=peer["run_id"],
        )["router_lifecycle"]

    assert lifecycle["current_state"] == "peer_ready"


def test_no_plaintext_private_key_leak_in_dry_run(app):
    peer = _prepared_peer(app)
    with app.app_context():
        result = ServerWireGuardPeerApplyService().dry_run(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
    )

    serialized = str(result)
    assert "server_private_key_ref" not in serialized
    assert "PRIVATEKEY" not in serialized


def test_unsupported_default_adapter_returns_blocked_not_exception(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", "true")
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService()
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert result["code"] == "server_apply_adapter_not_configured"


def test_rollback_with_mock_adapter_returns_peer_to_ready(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", "true")
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService(write_adapter=MockServerWireGuardWriteAdapter())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )
        result = service.rollback(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_rollback_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "rolled_back"


def test_v2_renders_server_peer_lab_panel(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["tenant_id"] = 1
            sess["_csrf_token"] = "test-csrf"
        res = client.get("/admin/radius/setup-wizard-v2")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "data-swv2-server-peer-dry-run" in html
    assert "data-swv2-server-peer-verify" in html
    assert "data-swv2-server-peer-result" in html
