from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {}
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
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "bridge.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app


def _enabled_config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    return AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="shared-secret-value",
        timeout_seconds=1.0,
        retry_count=0,
    )


def test_valid_mocked_license_response_is_accepted_and_snapshotted(app_db):
    from app.radius.services.admin_panel_client import (
        AdminBridgeSnapshotStore,
        AdminPanelClient,
        SNAPSHOT_LICENSE_CHECK,
    )

    transport = MockTransport(
        {
            "status": "active",
            "valid": True,
            "limits": {"subscribers": 1000, "nas": 50},
            "expires_at": "2026-12-31T23:59:59Z",
            "license_key": "lic_test_123456789",
        }
    )
    result = AdminPanelClient(config=_enabled_config(), transport=transport).check_license(
        tenant_id=7
    )

    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert result["payload"]["license_key"] == "lic_...6789"
    assert transport.calls[0]["timeout_seconds"] == 1.0
    assert transport.calls[0]["json_body"]["license_key"] == "lic_test_123456789"
    assert transport.calls[0]["headers"]["X-HobeRadius-Admin-Secret"] == "shared-secret-value"

    latest = AdminBridgeSnapshotStore().latest(
        tenant_id=7, snapshot_type=SNAPSHOT_LICENSE_CHECK
    )
    assert latest is not None
    assert latest["status"] == "healthy"
    assert latest["payload_json"]["license_key"] == "lic_...6789"


def test_invalid_license_payload_is_rejected_safely(app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient

    result = AdminPanelClient(
        config=_enabled_config(),
        transport=MockTransport({"valid": "yes", "limits": []}),
    ).check_license(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "invalid_payload"
    assert result["error"]["code"] == "invalid_payload"
    assert "status is required" in result["error"]["problems"]
    assert "valid must be boolean when present" in result["error"]["problems"]
    assert "limits must be an object when present" in result["error"]["problems"]


def test_admin_panel_timeout_is_handled_without_breaking_app(app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient

    result = AdminPanelClient(
        config=_enabled_config(),
        transport=MockTransport(exc=TimeoutError("slow admin panel")),
    ).check_license(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["error"]["code"] == "admin_panel_timeout"


def test_bridge_disabled_does_not_call_transport_or_require_config(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient

    transport = MockTransport({"status": "active"})
    config = AdminBridgeConfig(
        enabled=False,
        base_url="",
        license_key="",
        shared_secret="",
        timeout_seconds=1.0,
        retry_count=0,
    )
    result = AdminPanelClient(config=config, transport=transport).check_license(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert result["error"]["code"] == "bridge_disabled"
    assert transport.calls == []


def test_stale_snapshot_behavior_is_documented_and_tested(app_db):
    from app.radius.services.admin_panel_client import (
        AdminBridgeSnapshotStore,
        SNAPSHOT_LICENSE_CHECK,
    )

    store = AdminBridgeSnapshotStore()
    store.save(
        tenant_id=1,
        snapshot_type=SNAPSHOT_LICENSE_CHECK,
        status="healthy",
        source_url="https://admin.example.test/license",
        payload={"status": "active"},
        fetched_at="2000-01-01T00:00:00Z",
    )

    health = store.health(
        tenant_id=1,
        snapshot_type=SNAPSHOT_LICENSE_CHECK,
        stale_after_seconds=60,
    )

    assert health["stale"] is True
    assert health["status"] == "stale"
    assert health["snapshot"]["payload_json"]["status"] == "active"


def test_env_config_defaults_to_disabled_and_prefers_hoberadius_license(monkeypatch):
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    monkeypatch.delenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", raising=False)
    monkeypatch.setenv("HOBERADIUS_ADMIN_BASE_URL", "https://admin.example.test/")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "primary-license")
    monkeypatch.setenv("INSTANCE_LICENSE_KEY", "fallback-license")
    monkeypatch.setenv("HOBERADIUS_ADMIN_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("HOBERADIUS_ADMIN_RETRY_COUNT", "99")

    config = AdminBridgeConfig.from_env()

    assert config.enabled is False
    assert config.base_url == "https://admin.example.test"
    assert config.license_key == "primary-license"
    assert config.timeout_seconds == 30.0
    assert config.retry_count == 3
