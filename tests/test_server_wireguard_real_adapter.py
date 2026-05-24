from __future__ import annotations

import os
import secrets
from types import SimpleNamespace

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    get_setup_wizard_service,
)
from app.radius.services.setup_wizard_provisioning_orchestrator import (
    RouterProvisioningOrchestrator,
)
from app.radius.services.setup_wizard_server_wg import (
    MockServerWireGuardWriteAdapter,
    RealServerWireGuardWriteAdapter,
    ServerWireGuardPeerApplyService,
    server_peer_confirmation_phrase,
    server_peer_rollback_phrase,
)
from app.radius.services.setup_wizard_server_wg_readiness import (
    MockCommandRunner,
    ServerWireGuardReadinessService,
    SubprocessSafeCommandRunner,
)


VALID_KEY_1 = "E" * 43 + "="
VALID_KEY_2 = "F" * 43 + "="


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wg-real-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for name in (
        "HOBERADIUS_SETUP_WIZARD_LAB_MODE",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_INTERFACE", "wg0")
    monkeypatch.setenv("HOBERADIUS_WG_LISTEN_PORT", "51820")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_BACKUP_DIR", "/backup/wg")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_ROLLBACK_STRATEGY", "tagged-peer-remove")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_COMMAND_TIMEOUT", "2")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_INTERFACE_ALLOWLIST", "wg0")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
    from app import create_app

    return create_app()


def _enable_all(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS", "true")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER", "true")


def _prepared_peer(app, public_key: str = VALID_KEY_1) -> dict:
    with app.app_context():
        svc = get_setup_wizard_service()
        run = svc.create_run(tenant_id=1, actor="qa")
        svc.mark_verified(tenant_id=1, run_id=run["id"], step_key=STEP_INTERNET_VERIFICATION)
        plan = svc.generate_vpn_radius_script(
            tenant_id=1,
            run_id=run["id"],
            payload={"router_label": "Lab Router"},
        )
        registry_id = int(plan["router_provisioning"]["id"])
        result = RouterProvisioningOrchestrator().submit_router_public_key(
            tenant_id=1,
            registry_id=registry_id,
            public_key=public_key,
        )
        peer = result["prepared_wireguard_peer"]
        return {
            "run_id": run["id"],
            "registry_id": registry_id,
            "prepared_peer_id": int(peer["id"]),
            "router_vpn_ip": peer["router_vpn_ip"],
            "allowed_ips": peer["allowed_ips"],
        }


def _wg_show(public_key: str, allowed_ip: str, handshake: str = "12 seconds ago") -> str:
    return (
        "interface: wg0\n"
        "  public key: SERVERKEY\n"
        "  listening port: 51820\n\n"
        f"peer: {public_key}\n"
        f"  allowed ips: {allowed_ip}\n"
        f"  latest handshake: {handshake}\n"
    )


def _readiness() -> ServerWireGuardReadinessService:
    runner = MockCommandRunner(
        {
            "wg show wg0": "interface: wg0\n  listening port: 51820\n",
            "ip addr show wg0": "inet 10.10.0.1/24 scope global wg0\n",
            "systemctl is-active wg-quick@wg0": "active\n",
        }
    )
    env = {
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS": "true",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER": "true",
        "HOBERADIUS_WG_INTERFACE": "wg0",
        "HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP": "10.10.0.1",
        "HOBERADIUS_WG_LISTEN_PORT": "51820",
        "HOBERADIUS_WG_CONFIG_PATH": "/etc/wireguard/wg0.conf",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_BACKUP_DIR": "/backup/wg",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_ROLLBACK_STRATEGY": "tagged-peer-remove",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_COMMAND_TIMEOUT": "2",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_INTERFACE_ALLOWLIST": "wg0",
    }
    return ServerWireGuardReadinessService(env=env, runner=runner)


def test_real_adapter_disabled_by_default(app):
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService(readiness_service=_readiness())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert result["code"] == "server_wg_real_apply_flags_disabled"


@pytest.mark.parametrize(
    "missing_flag",
    [
        "HOBERADIUS_SETUP_WIZARD_LAB_MODE",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER",
    ],
)
def test_missing_any_flag_blocks_apply(app, monkeypatch, missing_flag):
    _enable_all(monkeypatch)
    monkeypatch.delenv(missing_flag, raising=False)
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService(readiness_service=_readiness())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert result["code"] == "server_wg_real_apply_flags_disabled"


def test_backup_failure_blocks_apply(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    with app.app_context():
        adapter = MockServerWireGuardWriteAdapter(backup_ok=False)
        service = ServerWireGuardPeerApplyService(write_adapter=adapter, readiness_service=_readiness())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert result["code"] == "server_wg_backup_failed"


def test_duplicate_public_key_blocks_apply(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    duplicate = _wg_show(VALID_KEY_1, "10.10.0.99/32")
    with app.app_context():
        adapter = MockServerWireGuardWriteAdapter(before_output=duplicate)
        service = ServerWireGuardPeerApplyService(write_adapter=adapter, readiness_service=_readiness())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert "duplicate WireGuard public key" in result["code"]


def test_duplicate_allowed_ip_blocks_apply(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    duplicate = _wg_show(VALID_KEY_2, peer["allowed_ips"])
    with app.app_context():
        adapter = MockServerWireGuardWriteAdapter(before_output=duplicate)
        service = ServerWireGuardPeerApplyService(write_adapter=adapter, readiness_service=_readiness())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert "duplicate WireGuard allowed IP" in result["code"]


def test_command_constructed_as_list_and_shell_false():
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner = SubprocessSafeCommandRunner(interface_allowlist=["wg0"], run_func=fake_run)
    result = runner.execute_wireguard_apply(
        interface="wg0",
        public_key=VALID_KEY_1,
        allowed_ips="10.10.0.2/32",
    )

    assert result["ok"] is True
    assert result["command_args"] == ["wg", "set", "wg0", "peer", VALID_KEY_1, "allowed-ips", "10.10.0.2/32"]
    assert calls[0][1]["shell"] is False
    assert isinstance(calls[0][0], list)


def test_apply_success_with_handshake_marks_verified(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    after = _wg_show(VALID_KEY_1, peer["allowed_ips"])
    with app.app_context():
        adapter = MockServerWireGuardWriteAdapter(after_apply_output=after)
        service = ServerWireGuardPeerApplyService(write_adapter=adapter, readiness_service=_readiness())
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "verified_handshake"
    assert adapter.commands[-1] == ["wg", "set", "wg0", "peer", VALID_KEY_1, "allowed-ips", peer["allowed_ips"]]


def test_apply_success_without_handshake_returns_pending(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    after = _wg_show(VALID_KEY_1, peer["allowed_ips"], "never")
    with app.app_context():
        service = ServerWireGuardPeerApplyService(
            write_adapter=MockServerWireGuardWriteAdapter(after_apply_output=after),
            readiness_service=_readiness(),
        )
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "applied_no_handshake"


def test_verify_failure_after_apply_records_failed_verification(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService(
            write_adapter=MockServerWireGuardWriteAdapter(after_apply_output=""),
            readiness_service=_readiness(),
        )
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.apply(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_confirmation_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "failed_verification"
    assert result["verify_status"] == "missing_peer"


def test_rollback_removes_exact_public_key(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    after = _wg_show(VALID_KEY_1, peer["allowed_ips"], "never")
    adapter = MockServerWireGuardWriteAdapter(after_apply_output=after, after_rollback_output="")
    with app.app_context():
        service = ServerWireGuardPeerApplyService(write_adapter=adapter, readiness_service=_readiness())
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
    assert adapter.commands[-1] == ["wg", "set", "wg0", "peer", VALID_KEY_1, "remove"]


def test_rollback_refuses_without_applied_operation(app, monkeypatch):
    _enable_all(monkeypatch)
    peer = _prepared_peer(app)
    with app.app_context():
        service = ServerWireGuardPeerApplyService(
            write_adapter=MockServerWireGuardWriteAdapter(),
            readiness_service=_readiness(),
        )
        service.dry_run(tenant_id=1, prepared_peer_id=peer["prepared_peer_id"])
        result = service.rollback(
            tenant_id=1,
            prepared_peer_id=peer["prepared_peer_id"],
            confirmation=server_peer_rollback_phrase(peer["prepared_peer_id"]),
        )

    assert result["status"] == "blocked"
    assert result["code"] == "applied_operation_required"


def test_private_keys_masked_from_backup():
    def fake_run(args, **kwargs):
        if args[:2] == ["wg", "showconf"]:
            return SimpleNamespace(returncode=0, stdout="[Interface]\nPrivateKey = SECRET\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="interface: wg0\n", stderr="")

    adapter = RealServerWireGuardWriteAdapter(
        runner=SubprocessSafeCommandRunner(interface_allowlist=["wg0"], run_func=fake_run)
    )
    backup = adapter.capture_backup(peer={"id": 1}, interface="wg0")

    assert "SECRET" not in str(backup)
    assert "PrivateKey = ***" in backup["wg_showconf"]


def test_dangerous_command_blocked():
    runner = SubprocessSafeCommandRunner(interface_allowlist=["wg0"])
    result = runner.execute_read_only("wg-quick down wg0")

    assert result["blocked"] is True
    assert result["code"] == "dangerous_command"
