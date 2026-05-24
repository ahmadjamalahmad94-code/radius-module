from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.setup_wizard_server_wg_readiness import (
    CommandSafetyClassifier,
    DisabledCommandRunner,
    MockCommandRunner,
    ServerWireGuardReadinessService,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wg-ready-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS", raising=False)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY", raising=False)
    monkeypatch.delenv("HOBERADIUS_SETUP_WIZARD_LAB_MODE", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    reset_for_tests(os.path.join(tmp_path, "test.db"))
    from app import create_app

    return create_app()


def _env(**extra: str) -> dict[str, str]:
    base = {
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS": "true",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER": "true",
        "HOBERADIUS_WG_INTERFACE": "wg0",
        "HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP": "10.10.0.1",
        "HOBERADIUS_WG_LISTEN_PORT": "51820",
        "HOBERADIUS_WG_CONFIG_PATH": "/etc/wireguard/wg0.conf",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_BACKUP_DIR": "/var/backups/hoberadius/wg",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_ROLLBACK_STRATEGY": "tagged-peer-remove",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_COMMAND_TIMEOUT": "2",
        "HOBERADIUS_SETUP_WIZARD_SERVER_WG_INTERFACE_ALLOWLIST": "wg0",
    }
    base.update(extra)
    return base


def _runner() -> MockCommandRunner:
    return MockCommandRunner(
        {
            "wg show wg0": "interface: wg0\n  public key: SERVERKEY\n  listening port: 51820\n",
            "ip addr show wg0": "7: wg0: <POINTOPOINT,UP>\n    inet 10.10.0.1/24 scope global wg0\n",
        }
    )


def test_readiness_disabled_by_default():
    result = ServerWireGuardReadinessService(env={}, runner=_runner()).evaluate()

    assert result["status"] == "disabled"
    assert result["configured"] is False
    assert result["diagnostics"][0]["code"] == "server_wg_readiness_disabled"


def test_missing_interface_config_returns_blocked():
    env = _env(HOBERADIUS_WG_INTERFACE="")
    result = ServerWireGuardReadinessService(env=env, runner=_runner()).evaluate()

    assert result["status"] == "blocked"
    assert result["checks"]["interface_configured"]["status"] == "blocked"
    assert any(item["code"] == "missing_wg_interface" for item in result["diagnostics"])


def test_mock_ready_environment_returns_ready():
    result = ServerWireGuardReadinessService(env=_env(), runner=_runner()).evaluate()

    assert result["status"] == "ready"
    assert result["checks"]["wg_show_readable"]["status"] == "success"
    assert result["checks"]["server_ip_assigned"]["status"] == "success"


def test_missing_backup_returns_partial():
    env = _env(HOBERADIUS_SETUP_WIZARD_SERVER_WG_BACKUP_DIR="")
    result = ServerWireGuardReadinessService(env=env, runner=_runner()).evaluate()

    assert result["status"] == "partial"
    assert result["checks"]["backup_dir_configured"]["status"] == "warning"
    assert any(item["code"] == "missing_backup_dir" for item in result["diagnostics"])


def test_dangerous_command_classified_as_dangerous():
    classifier = CommandSafetyClassifier()

    assert classifier.classify("wg-quick down wg0").kind == "dangerous"
    assert classifier.classify("systemctl restart wg-quick@wg0").kind == "dangerous"
    assert classifier.classify("wg set wg0 peer KEY allowed-ips 10.10.0.2/32").kind == "write"
    assert classifier.classify("wg show wg0").allowed_read_only is True


def test_disabled_runner_executes_nothing():
    runner = DisabledCommandRunner()
    result = runner.execute_read_only("wg show wg0")

    assert result["blocked"] is True
    assert result["code"] == "command_runner_disabled"
    assert runner.commands == []


def test_mock_runner_allows_read_only_wg_show():
    runner = _runner()
    result = runner.execute_read_only("wg show wg0")

    assert result["ok"] is True
    assert runner.commands == ["wg show wg0"]


def test_endpoint_returns_json(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["tenant_id"] = 1
            sess["_csrf_token"] = "test-csrf"
        res = client.get("/admin/radius/setup-wizard/server-wg/readiness")
        body = res.get_json()

    assert res.status_code == 200
    assert body["ok"] is True
    assert body["readiness"]["status"] == "disabled"


def test_v2_renders_readiness_section(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["tenant_id"] = 1
            sess["_csrf_token"] = "test-csrf"
        res = client.get("/admin/radius/setup-wizard-v2")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "data-swv2-server-wg-readiness" in html
    assert "data-swv2-server-wg-readiness-check" in html
    assert "data-swv2-server-wg-readiness-result" in html
