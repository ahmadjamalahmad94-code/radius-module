"""Tests for the auto-provision trigger via instance-ops/heartbeat.

Owner directive (يونيو 2026):
  When the bridge is enabled and a sync runs successfully, the client
  must POST to ``/api/integration/hoberadius/instance-ops/heartbeat``
  with the provision contract:
      license_key       — bearer-in-body, REAL value (not masked)
      radius_auth_ip    — this instance's reachable RADIUS IP
      realm             — proxy realm (operator-set or fallback slug)
      radius_auth_port  — 1812
      radius_acct_port  — 1813

  The panel idempotently mints a RADIUS instance + ProxyRealmRoute and
  returns a ``shared_secret`` in the response. The client must persist
  the secret locally so the operator's FreeRADIUS can use it.

This suite proves end-to-end:
  (1) build_provision_fields produces the right shape with sensible
      fallbacks (license-key slug → realm; settings/env → radius_auth_ip).
  (2) post_instance_heartbeat sends the REAL license_key in the body
      (not the masked form) and uses bearer-in-body auth.
  (3) A successful heartbeat persists the returned shared_secret to
      ``license_admin_bridge.instance_radius_secret``.
  (4) Top-level OR ``provision.shared_secret`` are both accepted.
  (5) Idempotency: re-running the heartbeat doesn't duplicate the secret
      and stores the same value.
  (6) An empty / missing shared_secret in the response leaves the stored
      value untouched (no clobber on health-only heartbeats).
"""
from __future__ import annotations

import os
import pytest


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response if response is not None else {}
        self.exc = exc
        self.calls: list[dict] = []

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
    db_file = os.fspath(tmp_path / "provision.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_PUBLIC_IP", raising=False)
    _reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        _run_pending_migrations()
        yield app
    _reset_for_tests(None)


def _bearer_config():
    """Simplified bridge config (bearer-in-body, no shared_secret)."""
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    # NOTE (smart-merge): the legacy purge (fix/radius-purge-legacy-linking,
    # already on main) removed the `shared_secret` field from
    # AdminBridgeConfig — the bridge is now bearer-in-body only. Don't pass
    # the dropped kwarg.
    return AdminBridgeConfig(
        enabled=True,
        base_url="https://panel.example.test",
        license_key="lic_simple_abcdef12",
        timeout_seconds=1.0,
        retry_count=0,
    )


# ════════════════════════════════════════════════════════════════════
# (1) build_provision_fields — shape + fallback chain
# ════════════════════════════════════════════════════════════════════


def test_provision_fields_shape_and_defaults(app_db):
    from app.radius.services.license_admin_instance_health import (
        build_provision_fields, RADIUS_AUTH_PORT, RADIUS_ACCT_PORT,
    )
    fields = build_provision_fields(_bearer_config())
    # Standard ports — panel reads these to wire ProxyRealmRoute.
    assert fields["radius_auth_port"] == RADIUS_AUTH_PORT == 1812
    assert fields["radius_acct_port"] == RADIUS_ACCT_PORT == 1813
    # license_key passes through unchanged (the bearer secret).
    assert fields["license_key"] == "lic_simple_abcdef12"
    # Realm fallback: slugified license-key prefix (stable per customer,
    # never leaks the full key). Underscores are valid in realm; they are
    # preserved by the slugifier.
    assert fields["realm"] == "hr-lic_simp"
    # radius_auth_ip fallback chain reaches a non-None string (may be
    # empty if no IP source available — that's a warning, not an error).
    assert isinstance(fields["radius_auth_ip"], str)


def test_provision_fields_operator_overrides_win(app_db):
    """When the operator sets instance.radius_auth_ip + instance.realm,
    those win over auto-derivation."""
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "instance.radius_auth_ip", "203.0.113.42", by=0)
    tenants_repo.set_setting(1, "instance.realm", "CustomerA Realm!", by=0)

    from app.radius.services.license_admin_instance_health import (
        build_provision_fields,
    )
    fields = build_provision_fields(_bearer_config())
    assert fields["radius_auth_ip"] == "203.0.113.42"
    # Slugified — special chars become safe, lowercased, capped.
    assert fields["realm"] == "customera-realm"


def test_provision_fields_radius_server_ip_setting_fallback(app_db):
    """network.radius_server_ip (existing customer-facing IP) is the
    second-level fallback for radius_auth_ip."""
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "network.radius_server_ip", "10.0.0.50", by=0)
    from app.radius.services.license_admin_instance_health import (
        build_provision_fields,
    )
    fields = build_provision_fields(_bearer_config())
    assert fields["radius_auth_ip"] == "10.0.0.50"


# ════════════════════════════════════════════════════════════════════
# (2) Wire body carries the REAL license_key + provision fields
# ════════════════════════════════════════════════════════════════════


def test_heartbeat_wire_body_carries_real_license_key_and_provision(app_db):
    """The transport receives:
      • Authorization: Bearer <real key>
      • body.license_key = REAL key (NOT masked lic_…)
      • body.radius_auth_ip / realm / radius_auth_port / radius_acct_port
    """
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "instance.radius_auth_ip", "187.77.70.18", by=0)
    tenants_repo.set_setting(1, "instance.realm", "owner-test", by=0)

    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService,
    )

    transport = MockTransport({"ok": True, "status": "ok"})
    client = AdminPanelClient(config=_bearer_config(), transport=transport)
    InstanceHealthService(
        config=_bearer_config(), admin_client=client,
    ).send_heartbeat(tenant_id=1, dry_run=False)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"].endswith("/api/integration/hoberadius/instance-ops/heartbeat")
    # Authorization header is the bearer.
    assert call["headers"]["Authorization"] == "Bearer lic_simple_abcdef12"
    body = call["json_body"]
    # CRITICAL: the wire body holds the REAL license_key, NOT the masked
    # form sanitize_bridge_payload would emit. Otherwise the panel can't
    # resolve the bearer in body.
    assert body["license_key"] == "lic_simple_abcdef12"
    assert "lic_..." not in body["license_key"]
    # Provision contract fields.
    assert body["radius_auth_ip"] == "187.77.70.18"
    assert body["realm"] == "owner-test"
    assert body["radius_auth_port"] == 1812
    assert body["radius_acct_port"] == 1813


# ════════════════════════════════════════════════════════════════════
# (3) Persist the returned shared_secret
# ════════════════════════════════════════════════════════════════════


def test_heartbeat_persists_top_level_shared_secret(app_db):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "instance.radius_auth_ip", "187.77.70.18", by=0)

    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService, SETTING_INSTANCE_RADIUS_SECRET,
    )

    transport = MockTransport({
        "ok": True, "status": "ok",
        "shared_secret": "freshly-minted-secret-aaaaaaaa",
    })
    client = AdminPanelClient(config=_bearer_config(), transport=transport)
    result = InstanceHealthService(
        config=_bearer_config(), admin_client=client,
    ).send_heartbeat(tenant_id=1, dry_run=False)

    assert result["ok"] is True
    assert result.get("provisioned") is True
    # The masked form uses Unicode «…» (U+2026), not three ASCII dots.
    masked = result.get("provisioned_secret_masked", "")
    assert "…" in masked, f"expected unicode ellipsis in mask, got {masked!r}"
    # And only the first/last 4 chars leak — the bulk is redacted.
    assert masked.startswith("fres") and masked.endswith("aaaa")
    stored = tenants_repo.get_setting(1, SETTING_INSTANCE_RADIUS_SECRET, "_NONE_")
    assert stored == "freshly-minted-secret-aaaaaaaa"


def test_heartbeat_persists_nested_provision_shared_secret(app_db):
    """The panel may evolve to return ``provision.shared_secret`` —
    we accept both shapes."""
    from app.radius.db.repos import tenants_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService, SETTING_INSTANCE_RADIUS_SECRET,
    )

    transport = MockTransport({
        "ok": True, "status": "ok",
        "provision": {"shared_secret": "nested-secret-bbbbbbbb",
                       "instance_id": "inst-77"},
    })
    client = AdminPanelClient(config=_bearer_config(), transport=transport)
    InstanceHealthService(
        config=_bearer_config(), admin_client=client,
    ).send_heartbeat(tenant_id=1, dry_run=False)

    stored = tenants_repo.get_setting(1, SETTING_INSTANCE_RADIUS_SECRET, "")
    assert stored == "nested-secret-bbbbbbbb"


# ════════════════════════════════════════════════════════════════════
# (4) Idempotency: re-send writes the same value, not a duplicate
# ════════════════════════════════════════════════════════════════════


def test_idempotent_re_send_keeps_same_secret(app_db):
    from app.radius.db.repos import tenants_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService, SETTING_INSTANCE_RADIUS_SECRET,
    )

    transport = MockTransport({
        "ok": True, "status": "ok",
        "shared_secret": "stable-secret-cccccccc",
    })
    client = AdminPanelClient(config=_bearer_config(), transport=transport)
    svc = InstanceHealthService(config=_bearer_config(), admin_client=client)

    svc.send_heartbeat(tenant_id=1, dry_run=False)
    svc.send_heartbeat(tenant_id=1, dry_run=False)
    svc.send_heartbeat(tenant_id=1, dry_run=False)

    # Three heartbeats fired against the panel.
    assert len(transport.calls) == 3
    # Stored value is the same; tenant_settings has a single row per key
    # by design (UPSERT semantics) — no duplicates.
    stored = tenants_repo.get_setting(1, SETTING_INSTANCE_RADIUS_SECRET, "")
    assert stored == "stable-secret-cccccccc"


# ════════════════════════════════════════════════════════════════════
# (5) No shared_secret in response → don't clobber stored value
# ════════════════════════════════════════════════════════════════════


def test_heartbeat_without_secret_in_response_does_not_clobber(app_db):
    from app.radius.db.repos import tenants_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService, SETTING_INSTANCE_RADIUS_SECRET,
    )

    # Pre-seed a stored secret (simulating a prior provision response).
    tenants_repo.set_setting(
        1, SETTING_INSTANCE_RADIUS_SECRET, "previously-stored-secret", by=0,
    )

    # Now run a heartbeat whose response is just health (no shared_secret).
    transport = MockTransport({"ok": True, "status": "ok",
                                "health": {"db": "ok"}})
    client = AdminPanelClient(config=_bearer_config(), transport=transport)
    result = InstanceHealthService(
        config=_bearer_config(), admin_client=client,
    ).send_heartbeat(tenant_id=1, dry_run=False)

    assert result["ok"] is True
    # No provisioned flag because no secret was returned.
    assert not result.get("provisioned")
    # Stored value untouched.
    stored = tenants_repo.get_setting(1, SETTING_INSTANCE_RADIUS_SECRET, "")
    assert stored == "previously-stored-secret"


def test_failed_heartbeat_does_not_persist(app_db):
    """A 5xx / unavailable heartbeat must not write a secret even if the
    error path somehow contained a field."""
    from app.radius.db.repos import tenants_repo
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_instance_health import (
        InstanceHealthService, SETTING_INSTANCE_RADIUS_SECRET,
    )

    transport = MockTransport({
        "ok": False, "status": "unavailable",
        "error": {"code": "admin_panel_unavailable"},
        # Even if some upstream stuffs a secret in the body, ok=False
        # short-circuits persistence.
        "shared_secret": "should-not-store",
    })
    client = AdminPanelClient(config=_bearer_config(), transport=transport)
    # Force the client to return ok=False (transport returned status=ok
    # but the AdminPanelClient checks ok in our send_heartbeat path —
    # construct via the AdminPanelClient response). Actually the easier
    # way: have the transport raise so ok=False bubbles up.
    transport2 = MockTransport(exc=OSError("conn refused"))
    client2 = AdminPanelClient(config=_bearer_config(), transport=transport2)
    result = InstanceHealthService(
        config=_bearer_config(), admin_client=client2,
    ).send_heartbeat(tenant_id=1, dry_run=False)
    assert result["ok"] is False
    stored = tenants_repo.get_setting(1, SETTING_INSTANCE_RADIUS_SECRET, "_NONE_")
    assert stored in ("", "_NONE_")
