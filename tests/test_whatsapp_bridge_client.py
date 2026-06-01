"""Unit tests for the WhatsApp thin-client bridge methods on AdminPanelClient.

These run with NO network: a MockTransport records the bridge call and returns
a canned dict. We assert each of the five WhatsApp methods:
  * POSTs to the correct panel path,
  * sends a body that went through ``_license_check_payload`` (so it carries
    ``license_key`` + ``signature`` + the fingerprint envelope), and
  * parses the response dict.
We also assert a transport failure returns a safe dict (never raises), and a
grep-style guard asserts the route + template never touch Meta directly.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.radius.services.admin_panel_client import (
    WHATSAPP_ENQUEUE_PATH,
    WHATSAPP_MESSAGE_STATUS_PATH,
    WHATSAPP_PREFERENCES_SYNC_PATH,
    WHATSAPP_STATUS_PATH,
    WHATSAPP_TEST_PATH,
)

BASE = "https://admin.example.test"


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


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "whatsapp_bridge.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app
    from app.radius.db.migrations_runner import run_pending_migrations

    app = create_app()
    with app.app_context():
        run_pending_migrations()
        yield app
    _reset_for_tests(None)


def _enabled_config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    return AdminBridgeConfig(
        enabled=True,
        base_url=BASE,
        license_key="lic_test_123456789",
        shared_secret="shared-secret-value",
        timeout_seconds=1.0,
        retry_count=0,
    )


def _client(transport):
    from app.radius.services.admin_panel_client import AdminPanelClient

    return AdminPanelClient(config=_enabled_config(), transport=transport)


def _assert_signed_envelope(body: dict) -> None:
    """A bridge body that went through ``_license_check_payload`` carries the
    license key + the signed envelope fields."""
    from app.radius.services.admin_panel_client import sign_admin_bridge_payload

    assert body["license_key"] == "lic_test_123456789"
    assert body["server_fingerprint"]
    assert body["timestamp"]
    assert body["nonce"]
    assert body["signature"] == sign_admin_bridge_payload(body, "shared-secret-value")


# ──────────────────────────────────────────────────────────────────────────
# Each of the five methods posts to the correct path with a signed payload.
# ──────────────────────────────────────────────────────────────────────────
def test_get_whatsapp_status_posts_signed_to_status_path(app_db):
    transport = MockTransport({"status": "connected", "enabled": True, "connected": True})
    result = _client(transport).get_whatsapp_status()

    assert result["ok"] is True
    assert result["status"] == "connected"
    assert transport.calls[0]["url"] == f"{BASE}{WHATSAPP_STATUS_PATH}"
    assert transport.calls[0]["headers"]["X-HobeRadius-Admin-Secret"] == "shared-secret-value"
    _assert_signed_envelope(transport.calls[0]["json_body"])


def test_enqueue_whatsapp_message_posts_signed_with_event_fields(app_db):
    transport = MockTransport({"status": "queued", "idempotency_key": "idem-1"})
    payload = {
        "source_event_type": "near_expiry",
        "subscriber_id": 42,
        "recipient_phone": "962790000000",
        "template_key": "expiry_reminder",
        "language": "ar",
        "variables": {"days": 3},
        "idempotency_key": "idem-1",
    }
    result = _client(transport).enqueue_whatsapp_message(payload)

    assert result["ok"] is True
    assert result["status"] == "queued"
    assert transport.calls[0]["url"] == f"{BASE}{WHATSAPP_ENQUEUE_PATH}"
    body = transport.calls[0]["json_body"]
    # The caller's event fields are merged into the signed envelope.
    assert body["source_event_type"] == "near_expiry"
    assert body["subscriber_id"] == 42
    assert body["template_key"] == "expiry_reminder"
    assert body["variables"] == {"days": 3}
    assert body["idempotency_key"] == "idem-1"
    _assert_signed_envelope(body)


def test_send_whatsapp_test_posts_signed_to_test_path(app_db):
    transport = MockTransport({"status": "sent"})
    result = _client(transport).send_whatsapp_test(
        recipient_phone="962790000000", idempotency_key="wa-test-key"
    )

    assert result["ok"] is True
    assert result["status"] == "sent"
    assert transport.calls[0]["url"] == f"{BASE}{WHATSAPP_TEST_PATH}"
    body = transport.calls[0]["json_body"]
    assert body["recipient_phone"] == "962790000000"
    assert body["idempotency_key"] == "wa-test-key"
    _assert_signed_envelope(body)


def test_sync_subscriber_preferences_posts_signed_with_list(app_db):
    transport = MockTransport({"status": "ok", "synced": 2})
    subs = [
        {"subscriber_id": 1, "phone": "962790000001", "opt_in": True},
        {"subscriber_id": 2, "phone": "962790000002", "opt_in": False},
    ]
    result = _client(transport).sync_subscriber_preferences(subs)

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert transport.calls[0]["url"] == f"{BASE}{WHATSAPP_PREFERENCES_SYNC_PATH}"
    body = transport.calls[0]["json_body"]
    assert body["subscribers"] == subs
    _assert_signed_envelope(body)


def test_get_message_status_posts_signed_to_status_path(app_db):
    transport = MockTransport({"status": "delivered"})
    result = _client(transport).get_message_status(idempotency_key="idem-77")

    assert result["ok"] is True
    assert result["status"] == "delivered"
    assert transport.calls[0]["url"] == f"{BASE}{WHATSAPP_MESSAGE_STATUS_PATH}"
    body = transport.calls[0]["json_body"]
    assert body["idempotency_key"] == "idem-77"
    _assert_signed_envelope(body)


# ──────────────────────────────────────────────────────────────────────────
# A transport failure returns a safe dict — the methods NEVER raise.
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "invoke",
    [
        lambda c: c.get_whatsapp_status(),
        lambda c: c.enqueue_whatsapp_message({"template_key": "x"}),
        lambda c: c.send_whatsapp_test(recipient_phone="9620", idempotency_key="k"),
        lambda c: c.sync_subscriber_preferences([{"subscriber_id": 1}]),
        lambda c: c.get_message_status(idempotency_key="k"),
    ],
)
def test_transport_failure_returns_safe_dict_without_raising(app_db, invoke):
    transport = MockTransport(exc=TimeoutError("slow panel"))
    result = invoke(_client(transport))  # must not raise

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["error"]["code"] == "admin_panel_timeout"


def test_unreachable_panel_returns_safe_unavailable_dict(app_db):
    import urllib.error

    transport = MockTransport(exc=urllib.error.URLError("connection refused"))
    result = _client(transport).get_whatsapp_status()

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "admin_panel_unavailable"


def test_non_https_base_url_short_circuits_without_network(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient

    transport = MockTransport({"status": "connected"})
    config = AdminBridgeConfig(
        enabled=True,
        base_url="http://insecure.example.test",  # not https
        license_key="lic",
        shared_secret="secret",
        timeout_seconds=1.0,
        retry_count=0,
    )
    result = AdminPanelClient(config=config, transport=transport).get_whatsapp_status()

    assert result["ok"] is False
    assert result["status"] == "https_required"
    # No network call attempted.
    assert transport.calls == []


def test_disabled_bridge_returns_safe_dict_without_network(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient

    transport = MockTransport({"status": "connected"})
    config = AdminBridgeConfig(
        enabled=False,
        base_url=BASE,
        license_key="lic",
        shared_secret="secret",
        timeout_seconds=1.0,
        retry_count=0,
    )
    result = AdminPanelClient(config=config, transport=transport).get_whatsapp_status()

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert transport.calls == []


# ──────────────────────────────────────────────────────────────────────────
# GUARD: the route + template are a thin client — no Meta endpoint, no token
# persistence anywhere in the WhatsApp client surface.
# ──────────────────────────────────────────────────────────────────────────
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_guard_no_meta_endpoint_or_token_persistence_in_client_surface():
    root = _repo_root()
    files = [
        root / "app" / "radius" / "routes" / "whatsapp.py",
        root / "app" / "templates" / "radius" / "whatsapp.html",
        root / "app" / "radius" / "services" / "admin_panel_client.py",
    ]
    forbidden_endpoints = ("graph.facebook.com", "facebook.com/v", "https://graph.")
    forbidden_secrets = (
        "meta_token",
        "meta_access_token",
        "whatsapp_token",
        "wa_token",
        "access_token",
        "waba_id",
        "app_secret",
        "verify_token",
        "phone_number_id",
    )
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for needle in forbidden_endpoints:
            assert needle not in text, f"{path.name} must not reference {needle!r}"
        for needle in forbidden_secrets:
            assert needle not in text, f"{path.name} must not handle a Meta secret {needle!r}"
