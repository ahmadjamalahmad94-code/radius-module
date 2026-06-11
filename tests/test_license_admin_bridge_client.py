from __future__ import annotations

import os

import pytest


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


def _reset_for_tests(db_file: str | None) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "bridge.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    app = create_app()
    with app.app_context():
        _run_pending_migrations()
        yield app
    _reset_for_tests(None)


def _enabled_config():
    """SIMPLE_LINK config — bearer-in-body only (post 2026-06-11 purge)."""
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    return AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        timeout_seconds=1.0,
        retry_count=0,
    )


def test_successful_license_snapshot_fetch(app_db):
    """Bearer-in-body fetch (post-purge): Authorization header carries the
    real key, body carries license_key (masked in the stored snapshot),
    NO X-HobeRadius-Admin-Secret, NO signature/timestamp/nonce."""
    from app.radius.services.admin_panel_client import (
        AdminPanelClient,
        get_current_license_state,
    )

    transport = MockTransport(
        {
            "status": "active",
            "valid": True,
            "limits": {"subscribers": 1000, "nas": 50},
            "license_key": "lic_test_123456789",
            "stale_after_seconds": 600,
        }
    )
    result = AdminPanelClient(config=_enabled_config(), transport=transport).fetch_license_snapshot(
        tenant_id=7
    )

    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["payload"]["license_key"] == "lic_...6789"
    assert result["snapshot"]["stale_after_seconds"] == 600
    assert transport.calls[0]["url"] == "https://admin.example.test/api/license/check"
    assert transport.calls[0]["timeout_seconds"] == 1.0
    # Bearer header carries the real key.
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer lic_test_123456789"
    # Legacy signed-path header is GONE.
    assert "X-HobeRadius-Admin-Secret" not in transport.calls[0]["headers"]
    request_body = transport.calls[0]["json_body"]
    assert request_body["license_key"] == "lic_test_123456789"
    assert request_body["server_fingerprint"]
    # NO HMAC fields — these were removed permanently 2026-06-11.
    assert "signature" not in request_body
    assert "timestamp" not in request_body
    assert "nonce" not in request_body

    state = get_current_license_state(tenant_id=7)
    assert state["ok"] is True
    assert state["status"] == "active"
    assert state["last_success"]["payload_json"]["license_key"] == "lic_...6789"


def test_legacy_signing_helpers_are_gone():
    """The HMAC helpers were deleted 2026-06-11 — importing them must fail."""
    from app.radius.services import admin_panel_client as apc
    assert not hasattr(apc, "sign_admin_bridge_payload"), (
        "sign_admin_bridge_payload was supposed to be deleted by "
        "feat/radius-purge-legacy-linking"
    )
    assert not hasattr(apc, "canonical_admin_bridge_payload"), (
        "canonical_admin_bridge_payload was supposed to be deleted"
    )


def test_successful_capacity_contract_fetch(app_db):
    from app.radius.services.admin_panel_client import (
        AdminPanelClient,
        get_current_capacity_contract,
    )

    result = AdminPanelClient(
        config=_enabled_config(),
        transport=MockTransport(
            {
                "status": "active",
                "contract": {"plan": "pilot"},
                "limits": {"routers": 50},
            }
        ),
    ).fetch_capacity_contract(tenant_id=1)

    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["snapshot"]["payload_json"]["contract"]["plan"] == "pilot"
    state = get_current_capacity_contract(tenant_id=1)
    assert state["ok"] is True
    assert state["last_success"]["payload_json"]["limits"]["routers"] == 50


def test_admin_panel_timeout_does_not_break_app_and_uses_stale_snapshot(app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient

    client = AdminPanelClient(
        config=_enabled_config(),
        transport=MockTransport({"status": "active", "valid": True}),
    )
    first = client.fetch_license_snapshot(tenant_id=1)
    assert first["ok"] is True

    timeout = AdminPanelClient(
        config=_enabled_config(),
        transport=MockTransport(exc=TimeoutError("slow admin panel")),
    ).fetch_license_snapshot(tenant_id=1)

    assert timeout["ok"] is False
    assert timeout["status"] == "timeout"
    assert timeout["error"]["code"] == "admin_panel_timeout"
    assert timeout["state"]["ok"] is True
    assert timeout["state"]["last_success"]["payload_json"]["status"] == "active"


def test_stale_snapshot_state(app_db):
    from app.radius.services.admin_panel_client import (
        LicenseAdminSnapshotStore,
        SNAPSHOT_LICENSE,
        get_current_license_state,
    )

    LicenseAdminSnapshotStore().save(
        tenant_id=1,
        snapshot_type=SNAPSHOT_LICENSE,
        normalized_status="active",
        source_url="https://admin.example.test/api/license/check",
        payload={"status": "active"},
        fetched_at="2000-01-01T00:00:00Z",
        stale_after_seconds=60,
    )

    state = get_current_license_state(tenant_id=1)
    assert state["ok"] is True
    assert state["stale"] is True
    assert state["status"] == "stale"


def test_invalid_payload_rejected_safely(app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient

    result = AdminPanelClient(
        config=_enabled_config(),
        transport=MockTransport({"valid": "yes", "limits": []}),
    ).fetch_license_snapshot(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "invalid_payload"
    assert result["error"]["code"] == "invalid_payload"
    assert "status is required" in result["error"]["problems"]
    assert "valid must be boolean when present" in result["error"]["problems"]
    assert "limits must be an object when present" in result["error"]["problems"]


def test_missing_env_or_disabled_bridge_disables_remote_fetch_safely(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient

    transport = MockTransport({"status": "active"})
    config = AdminBridgeConfig(
        enabled=False,
        base_url="",
        license_key="",
        timeout_seconds=1.0,
        retry_count=0,
    )

    result = AdminPanelClient(config=config, transport=transport).fetch_license_snapshot(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert result["error"]["code"] == "bridge_disabled"
    assert result["state"]["status"] == "unknown"
    assert transport.calls == []


def test_post_customer_service_request_sends_bearer_ticket_payload(app_db):
    """Service-request POST goes through the same bearer-in-body path —
    no HMAC signature on the body (post-purge)."""
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = MockTransport({
        "ok": True,
        "status": "pending",
        "service_request": {"reference": "SR-ABC12345"},
    })

    result = AdminPanelClient(config=_enabled_config(), transport=transport).post_customer_service_request(
        service_key="cards",
        request_type="activation",
        notes="طلب تفعيل من الريدياس",
        desired_limits={"generate_per_batch": 100},
    )

    assert result["ok"] is True
    assert result["status"] == "pending"
    assert transport.calls[0]["url"] == "https://admin.example.test/api/integration/hoberadius/service-requests"
    payload = transport.calls[0]["json_body"]
    assert payload["service_key"] == "cards"
    assert payload["request_type"] == "activation"
    assert payload["desired_limits"]["generate_per_batch"] == 100
    # No signature; the body carries license_key + Authorization header is set.
    assert "signature" not in payload
    assert payload["license_key"] == "lic_test_123456789"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer lic_test_123456789"


def test_env_config_clamps_timeout_retry_and_prefers_hoberadius_license(monkeypatch):
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BASE_URL", "https://admin.example.test/")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "primary-license")
    monkeypatch.setenv("INSTANCE_LICENSE_KEY", "fallback-license")
    monkeypatch.setenv("HOBERADIUS_ADMIN_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("HOBERADIUS_ADMIN_RETRY_COUNT", "99")

    config = AdminBridgeConfig.from_env()

    assert config.enabled is True
    assert config.base_url == "https://admin.example.test"
    assert config.license_key == "primary-license"
    assert config.timeout_seconds == 30.0
    assert config.retry_count == 3
