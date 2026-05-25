from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {"ok": True, "status": "ok"}
        self.exc = exc
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "heartbeat.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def test_health_payload_generated_with_minimal_environment(app_db, monkeypatch):
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_123456789")
    monkeypatch.setenv("HOBERADIUS_INSTANCE_ID", "inst-1")

    payload = InstanceHealthService().build_payload(tenant_id=1)

    assert payload["module"] == "radius-module"
    assert payload["instance_id"] == "inst-1"
    assert payload["license_key"] == "lic_...6789"
    assert payload["db"]["status"] == "ok"
    assert payload["freeradius"]["status"] == "unknown"
    assert payload["accounting"]["radacct_table"] in {True, False}
    assert "idempotency_key" in payload


def test_remote_success_is_stored(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    transport = MockTransport(response={"ok": True, "status": "healthy", "secret": "hidden"})
    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="shared",
        timeout_seconds=1,
        retry_count=0,
    )
    service = InstanceHealthService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=transport),
    )

    result = service.send_heartbeat(tenant_id=1, dry_run=False)

    assert result["ok"] is True
    assert result["attempt"]["status"] == "sent"
    assert result["attempt"]["sent_at"]
    assert transport.calls[0]["url"].endswith("/api/integration/hoberadius/instance-ops/heartbeat")
    assert result["response"]["secret"] == "***"


def test_remote_failure_is_stored_but_app_continues(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    service = InstanceHealthService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=MockTransport(exc=TimeoutError("slow"))),
    )

    result = service.send_heartbeat(tenant_id=1, dry_run=False)

    assert result["ok"] is False
    assert result["attempt"]["status"] == "timeout"
    assert result["attempt"]["error_json"]["code"] == "admin_panel_timeout"


def test_missing_optional_checks_do_not_crash(app_db, monkeypatch):
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    monkeypatch.delenv("HOBERADIUS_LICENSE_KEY", raising=False)
    payload = InstanceHealthService().build_payload(tenant_id=1)

    assert payload["backup"]["status"] in {"unknown", "success", "failed", "ok"}
    assert payload["scheduler"]["status"] in {"unknown", "ok"}


def test_invalid_admin_config_disables_send_safely(app_db):
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    result = InstanceHealthService().send_heartbeat(tenant_id=1, dry_run=False)

    assert result["ok"] is False
    assert result["attempt"]["status"] in {"disabled", "config_missing"}
    assert result["attempt"]["error_json"]["code"] in {"bridge_disabled", "config_missing"}


def test_manual_heartbeat_route_defaults_to_dry_run(client):
    res = client.post(
        "/api/v1/system/admin-bridge/heartbeat",
        json={},
        headers=AUTH,
    )

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["dry_run"] is True
    assert data["payload"]["module"] == "radius-module"
    assert data["attempt"]["status"] == "dry_run"
