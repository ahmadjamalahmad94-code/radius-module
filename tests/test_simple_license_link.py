"""Tests for the SIMPLIFIED licensing-link contract (يونيو 2026).

The owner's directive: only the license key (16-char) is required. The
panel accepts it as a bearer secret in the request BODY plus the optional
``Authorization: Bearer <key>`` header. No HMAC signature, no derived
"bind secret", no required fingerprint.

This suite proves the client honours the simplified contract end-to-end:
  (1) When no ``shared_secret`` is stored, the client sends bearer-in-body
      (no signature, no nonce, no timestamp) and a matching Authorization
      header.
  (2) When a ``shared_secret`` IS stored (legacy), the signed path keeps
      working unchanged.
  (3) The transport surfaces 403 + reason=customer_pending as a normal
      status so the route layer can map it to the Arabic friendly
      message instead of a cryptic "unavailable".
  (4) The 2-field setup persists with NO shared_secret + NO fingerprint
      (form omitted).
  (5) Backup upload works with just the license key (bearer path).
  (6) ``mask_license_key`` redacts safely for log lines.

Mocks the panel via ``MockTransport`` (no real HTTP).
"""
from __future__ import annotations

import io
import json
import os
from urllib.error import HTTPError

import pytest


class MockTransport:
    """In-memory transport. Records every call; returns canned responses."""

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
    db_file = os.fspath(tmp_path / "simple_link.db")
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


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _bearer_config():
    """Simplified config: license_key set, shared_secret EMPTY."""
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True,
        base_url="https://panel.example.test",
        license_key="lic_simple_abcdef12",
        shared_secret="",          # <— the whole point of the simplified path
        timeout_seconds=1.0,
        retry_count=0,
    )


def _signed_config():
    """Legacy config: shared_secret present → signed path."""
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True,
        base_url="https://panel.example.test",
        license_key="lic_legacy_abcdef12",
        shared_secret="shared-secret-value",
        timeout_seconds=1.0,
        retry_count=0,
    )


# ════════════════════════════════════════════════════════════════════
# (1) Bearer-in-body — no signature, no nonce, no timestamp
# ════════════════════════════════════════════════════════════════════


def test_bearer_path_sends_authorization_header_and_no_signature(app_db):
    """Simplified config → headers carry ``Authorization: Bearer <key>``,
    body carries ``license_key``, NO signature/timestamp/nonce."""
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = MockTransport({
        "status": "active", "valid": True,
        "limits": {"subscribers": 100, "nas": 5},
    })
    AdminPanelClient(config=_bearer_config(), transport=transport).fetch_license_snapshot(
        tenant_id=1,
    )

    call = transport.calls[0]
    # Header carries the bearer.
    assert call["headers"]["Authorization"] == "Bearer lic_simple_abcdef12"
    # Legacy header MUST be absent on the simplified path.
    assert "X-HobeRadius-Admin-Secret" not in call["headers"]
    # Body carries license_key (authoritative bearer copy).
    body = call["json_body"]
    assert body["license_key"] == "lic_simple_abcdef12"
    # NO HMAC fields.
    assert "signature" not in body
    assert "timestamp" not in body
    assert "nonce" not in body


def test_signed_path_remains_backwards_compatible(app_db):
    """Legacy config (with shared_secret) → headers carry the legacy
    ``X-HobeRadius-Admin-Secret``, body carries signature/timestamp/nonce.
    NO bearer header (one auth scheme per request — no double-auth)."""
    from app.radius.services.admin_panel_client import (
        AdminPanelClient, sign_admin_bridge_payload,
    )

    transport = MockTransport({"status": "active", "valid": True})
    AdminPanelClient(config=_signed_config(), transport=transport).fetch_license_snapshot(
        tenant_id=1,
    )

    call = transport.calls[0]
    assert call["headers"]["X-HobeRadius-Admin-Secret"] == "shared-secret-value"
    # No bearer Authorization in the signed path.
    assert "Authorization" not in call["headers"]
    body = call["json_body"]
    # Signed fields are still emitted.
    assert "signature" in body and "timestamp" in body and "nonce" in body
    assert body["signature"] == sign_admin_bridge_payload(body, "shared-secret-value")


def test_bearer_path_omits_shared_secret_from_missing_fields(app_db):
    """Simplified config has NO shared_secret — ``missing_fields()`` must
    NOT complain about that. Only base_url + license_key matter now."""
    cfg = _bearer_config()
    assert cfg.missing_fields() == []


# ════════════════════════════════════════════════════════════════════
# (2) Backup upload via bearer key — no admin_secret in body
# ════════════════════════════════════════════════════════════════════


def test_backup_upload_uses_bearer_on_simple_path(app_db):
    """Backup upload on the simplified path: license_key in body + bearer
    header, NO admin_secret field."""
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = MockTransport({"ok": True, "status": "stored",
                                "backup_id": "bkp_123"})
    result = AdminPanelClient(config=_bearer_config(), transport=transport).post_backup_upload(
        payload={"filename": "backup.sqlite3", "content_b64": "AAAA"},
    )
    assert result["ok"] is True
    assert result["status"] == "stored"

    call = transport.calls[0]
    assert call["url"].endswith("/api/integration/hoberadius/backups/upload")
    body = call["json_body"]
    # License key carried in body (authoritative bearer copy).
    assert body["license_key"] == "lic_simple_abcdef12"
    # NO admin_secret on the simple path — the key itself is the secret.
    assert "admin_secret" not in body
    # Authorization header present.
    assert call["headers"]["Authorization"] == "Bearer lic_simple_abcdef12"
    assert "X-HobeRadius-Admin-Secret" not in call["headers"]


def test_backup_upload_signed_path_still_carries_admin_secret(app_db):
    """Backwards-compat: a configured shared_secret still gets serialized
    into the backup-upload body so legacy panels keep accepting it."""
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = MockTransport({"ok": True, "status": "stored"})
    AdminPanelClient(config=_signed_config(), transport=transport).post_backup_upload(
        payload={"filename": "b.sqlite3", "content_b64": "BBBB"},
    )
    body = transport.calls[0]["json_body"]
    assert body["admin_secret"] == "shared-secret-value"
    assert body["license_key"] == "lic_legacy_abcdef12"


# ════════════════════════════════════════════════════════════════════
# (3) customer_pending — 403 surfaces as a friendly status, not unavailable
# ════════════════════════════════════════════════════════════════════


def _http_error_with_body(status: int, body: dict) -> HTTPError:
    """Build an HTTPError carrying a JSON body, as the panel would return."""
    raw = json.dumps(body).encode("utf-8")
    return HTTPError(
        url="https://panel.example.test/api/license/check",
        code=status,
        msg=f"HTTP {status}",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(raw),
    )


def test_transport_parses_403_customer_pending(app_db):
    """The transport must read 4xx JSON bodies (not just raise URLError),
    promoting ``reason: customer_pending`` to ``status`` so the route layer
    can map it to the owner's Arabic friendly message."""
    from app.radius.services.admin_panel_client import (
        AdminPanelClient, UrlLibAdminBridgeTransport,
    )

    # Build a real urllib transport, then patch urlopen to raise the 403.
    transport = UrlLibAdminBridgeTransport()
    import urllib.request as _req
    err = _http_error_with_body(403, {
        "ok": False,
        "reason": "customer_pending",
        "message": "Customer account not active yet",
    })
    saved_urlopen = _req.urlopen
    try:
        _req.urlopen = lambda *a, **kw: (_ for _ in ()).throw(err)  # type: ignore[assignment]
        result = AdminPanelClient(
            config=_bearer_config(), transport=transport,
        ).fetch_license_snapshot(tenant_id=1)
    finally:
        _req.urlopen = saved_urlopen

    # Status is the friendly key the route layer recognises.
    assert result["status"] == "customer_pending"


def test_status_label_for_customer_pending_is_owner_message(app_db):
    """The route helper ``_sync_status_label`` must surface the owner's
    exact Arabic message for customer_pending."""
    from app.radius.routes.admin_bridge import _sync_status_label
    label = _sync_status_label("customer_pending")
    # Exact phrasing from the directive.
    assert "حساب العميل غير مفعّل بعد في لوحة التراخيص" in label
    assert "صفحة العميل" in label
    assert "ثم أعد المحاولة" in label


def test_status_label_for_other_friendly_codes(app_db):
    """Sibling friendly statuses also map cleanly (not the raw code)."""
    from app.radius.routes.admin_bridge import _sync_status_label
    assert _sync_status_label("unauthorized").startswith("مفتاح الترخيص مرفوض")
    assert _sync_status_label("customer_inactive").startswith("حساب العميل غير مفعّل")


# ════════════════════════════════════════════════════════════════════
# (4) 2-field setup form — POST with only base_url + license_key works
# ════════════════════════════════════════════════════════════════════


def _login_super(client) -> None:
    """Make a super-admin and log them in (the licensing config screen
    is gated behind admin auth)."""
    from app.radius.db.repos import admins_repo
    from uuid import uuid4
    u = f"sl_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="sl-pass", full_name="SL Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "sl-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_setup_persists_with_only_url_and_key(client, app_db):
    """Submit the simplified form: just base_url + license_key + enabled.
    The route must save without complaining about missing shared_secret."""
    _login_super(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example.test",
            "license_key": "lic_user_2026abcd",
            "enabled": "1",
            # No shared_secret, no server_fingerprint — simplified path.
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}, res.get_data(as_text=True)

    # Verify persistence — the license_key is stored, shared_secret is NOT
    # written (would have flashed an error in the old flow).
    from app.radius.db.repos import tenants_repo
    saved = tenants_repo.get_setting(1, "license_admin_bridge.license_key", "")
    assert saved == "lic_user_2026abcd"
    assert tenants_repo.get_setting(
        1, "license_admin_bridge.shared_secret", "_NONE_"
    ) in ("", "_NONE_"), "shared_secret should NOT be written on simple path"


def test_setup_template_renders_two_field_form(client, app_db):
    """The form must show only 2 primary inputs (base_url + license_key);
    the legacy fields are nested inside a collapsible disclosure."""
    _login_super(client)
    res = client.get("/admin/radius/license-file")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Primary inputs.
    assert 'name="base_url"' in html
    assert 'name="license_key"' in html
    assert "data-simple-link-key" in html
    # Legacy inputs live under the collapsible disclosure.
    assert "data-license-advanced" in html
    assert 'class="hub-disclosure' in html
    # Hint text reflects the new contract.
    assert "هذا المفتاح هو سرّ الربط الوحيد" in html


# ════════════════════════════════════════════════════════════════════
# (5) mask_license_key — never leak full keys in logs
# ════════════════════════════════════════════════════════════════════


def test_mask_license_key_redacts():
    from app.radius.services.admin_panel_client import mask_license_key

    # Typical 16-char key → first 4 + … + last 4.
    assert mask_license_key("lic_simple_abcdef12") == "lic_…ef12"
    # Short key → first 2 + …
    assert mask_license_key("xy") == "xy…"
    # Empty / None → empty string (safe to interpolate anywhere).
    assert mask_license_key(None) == ""
    assert mask_license_key("") == ""
    assert mask_license_key("   ") == ""


def test_sanitize_bridge_payload_masks_license_key():
    """The dict-level sanitizer (used by every log line) masks license_key."""
    from app.radius.services.admin_panel_client import sanitize_bridge_payload
    s = sanitize_bridge_payload({
        "license_key": "lic_user_2026abcd",
        "status": "active",
        "nested": {"license_key": "lic_inner_xyzw"},
    })
    assert "lic_user_2026abcd" not in json.dumps(s, ensure_ascii=False)
    assert "lic_inner_xyzw" not in json.dumps(s, ensure_ascii=False)
    # Status passes through untouched.
    assert s["status"] == "active"
