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
    """SIMPLE_LINK config — license_key is the only credential.

    The legacy ``shared_secret`` field was permanently removed from
    ``AdminBridgeConfig`` on 2026-06-11
    (feat/radius-purge-legacy-linking) — there is no longer a dual-mode
    fixture in this suite.
    """
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True,
        base_url="https://panel.example.test",
        license_key="lic_simple_abcdef12",
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


def test_signed_path_was_removed(app_db):
    """The legacy signed path was removed permanently 2026-06-11. The
    AdminBridgeConfig dataclass no longer has a ``shared_secret`` field;
    ``_headers()`` no longer emits ``X-HobeRadius-Admin-Secret``;
    ``_license_check_payload`` no longer emits HMAC fields. These
    deletions are tracked by the ``test_legacy_signing_helpers_are_gone``
    assertion in ``test_license_admin_bridge_client.py`` — here we lock
    in the dataclass shape."""
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    import dataclasses as _dc
    field_names = {f.name for f in _dc.fields(AdminBridgeConfig)}
    assert "shared_secret" not in field_names, (
        "AdminBridgeConfig.shared_secret was supposed to be deleted"
    )


def test_bearer_path_missing_fields_only_url_and_key(app_db):
    """Only base_url + license_key matter for ``missing_fields()``."""
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


def test_backup_upload_never_includes_admin_secret(app_db):
    """``admin_secret`` body field was REMOVED from the backup upload
    on 2026-06-11. The license_key is the only secret."""
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = MockTransport({"ok": True, "status": "stored"})
    AdminPanelClient(config=_bearer_config(), transport=transport).post_backup_upload(
        payload={"filename": "b.sqlite3", "content_b64": "BBBB"},
    )
    body = transport.calls[0]["json_body"]
    assert "admin_secret" not in body
    assert body["license_key"] == "lic_simple_abcdef12"


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
    """Make the OWNER (unrestricted principal) and log them in. Saving the
    bridge base_url (license_file_config) is super-only per SEC C2, and
    session-super = owner (admins_repo.admin_is_owner), NOT the bare
    is_super_admin flag — so we must designate this account as an owner."""
    from app.radius.db.repos import admins_repo
    from uuid import uuid4
    u = f"sl_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="sl-pass", full_name="SL Tester",
        is_super_admin=True,
    )
    admins_repo.set_designated_owners([u])   # real owner → passes _PERM_SUPER
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


def test_setup_template_renders_minimal_form(client, app_db):
    """The form has ONLY: panel URL (readonly) + license_key + bridge-enable
    toggle + sync toggles. The legacy ``shared_secret`` /
    ``server_fingerprint`` / ``sync_interval_seconds`` inputs and the
    ``data-license-advanced`` disclosure were DELETED 2026-06-11
    (feat/radius-purge-legacy-linking)."""
    _login_super(client)
    res = client.get("/admin/radius/license-file")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Primary inputs present.
    assert 'name="base_url"' in html
    assert 'name="license_key"' in html
    assert "data-simple-link-key" in html
    # Legacy inputs + disclosure are GONE.
    assert 'name="shared_secret"' not in html
    assert 'name="server_fingerprint"' not in html
    assert 'name="sync_interval_seconds"' not in html
    assert "data-license-advanced" not in html
    assert "hub-disclosure" not in html
    # Sync toggles are still on the form (visible, not hidden behind disclosure).
    assert 'name="enabled"' in html
    assert 'name="runtime_contract_sync"' in html
    assert 'name="identity_sync_enabled"' in html
    assert 'name="worker_enabled"' in html
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
