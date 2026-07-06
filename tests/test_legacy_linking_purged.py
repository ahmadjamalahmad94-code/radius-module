"""Assertions that the legacy linking surface is permanently GONE.

If any of these fail it means someone re-introduced a legacy path on the
client. The branch ``feat/radius-purge-legacy-linking`` (merged on
2026-06-11) removed:

  1) ``AdminBridgeConfig.shared_secret`` field + signed-path branches in
     ``_headers()`` / ``_license_check_payload()`` / ``post_backup_upload``.
  2) ``sign_admin_bridge_payload`` + ``canonical_admin_bridge_payload``
     helpers (HMAC signers).
  3) ``app/radius/services/bridge_activation.py`` (one-time activation
     code flow) — file deleted.
  4) Routes ``/bridge/activate`` + ``/bridge/test`` — both 404 now.
  5) Template inputs ``name="shared_secret"`` / ``name="server_fingerprint"``
     / ``name="sync_interval_seconds"`` + the ``data-license-advanced``
     disclosure — gone from ``license_file.html``.
  6) JSON keys ``shared_secret_configured`` / ``sync_interval_seconds`` /
     ``server_fingerprint`` — removed from ``/api/v1/system/license-file``.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_purge_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ADMIN_SHARED_SECRET", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"pl_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="pl-pass", full_name="Purge Tester",
        is_super_admin=True,
    )
    # Saving the bridge config is owner-only (SEC C2); session-super = owner,
    # not the bare is_super_admin flag → designate this account as an owner.
    admins_repo.set_designated_owners([u])
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "pl-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


# ════════════════════════════════════════════════════════════════════
# (1) AdminBridgeConfig + auth surface
# ════════════════════════════════════════════════════════════════════


def test_admin_bridge_config_has_no_shared_secret_field():
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    import dataclasses as _dc
    names = {f.name for f in _dc.fields(AdminBridgeConfig)}
    assert "shared_secret" not in names


def test_admin_bridge_config_constructs_without_shared_secret():
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    cfg = AdminBridgeConfig(
        enabled=True, base_url="https://x.test",
        license_key="lic_test_abcdefgh",
        timeout_seconds=1.0, retry_count=0,
    )
    assert cfg.missing_fields() == []


def test_hmac_signing_helpers_were_removed():
    from app.radius.services import admin_panel_client as apc
    assert not hasattr(apc, "sign_admin_bridge_payload")
    assert not hasattr(apc, "canonical_admin_bridge_payload")


def test_hmac_uuid_imports_removed_from_admin_panel_client():
    """``hmac`` and ``uuid`` were only used by the signed-path code; once
    that's gone the imports should be too, so a future reviewer doesn't
    think they're available without a paper trail."""
    src = open(
        os.path.join(
            os.path.dirname(__file__), "..", "app", "radius", "services",
            "admin_panel_client.py",
        ),
        encoding="utf-8",
    ).read()
    # Top-level imports.
    assert "\nimport hmac\n" not in src
    assert "\nimport uuid\n" not in src


def test_bridge_activation_module_does_not_exist():
    """The dead activation-code service file is gone."""
    here = os.path.dirname(__file__)
    p = os.path.join(
        here, "..", "app", "radius", "services", "bridge_activation.py",
    )
    assert not os.path.exists(p), (
        "bridge_activation.py was supposed to be deleted by "
        "feat/radius-purge-legacy-linking"
    )
    # And the import must fail.
    with pytest.raises((ImportError, ModuleNotFoundError)):
        from app.radius.services import bridge_activation  # noqa: F401


# ════════════════════════════════════════════════════════════════════
# (2) Routes: /bridge/activate and /bridge/test return 404
# ════════════════════════════════════════════════════════════════════


def test_bridge_activate_endpoint_is_unmapped(app):
    """The Flask URL map must not contain ``radius.bridge_activate`` —
    proves the route was deleted, not just hidden by auth. Stronger than
    a 404 assertion (which the global auth middleware can mask with a
    redirect to /login when no session cookie is present)."""
    from werkzeug.routing.exceptions import BuildError
    with app.test_request_context():
        from flask import url_for
        with pytest.raises(BuildError):
            url_for("radius.bridge_activate")


def test_bridge_test_endpoint_is_unmapped(app):
    from werkzeug.routing.exceptions import BuildError
    with app.test_request_context():
        from flask import url_for
        with pytest.raises(BuildError):
            url_for("radius.bridge_test")


def test_url_map_has_no_bridge_legacy_rules(app):
    """Belt-and-braces: inspect the URL map for any rule that includes
    ``/bridge/activate`` or ``/bridge/test``."""
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert not any("/bridge/activate" in r for r in rules), (
        f"unexpected /bridge/activate rule found: {[r for r in rules if '/bridge/activate' in r]!r}"
    )
    assert not any("/bridge/test" in r for r in rules), (
        f"unexpected /bridge/test rule found: {[r for r in rules if '/bridge/test' in r]!r}"
    )


# ════════════════════════════════════════════════════════════════════
# (3) Template: legacy inputs + disclosure GONE; sync toggles VISIBLE
# ════════════════════════════════════════════════════════════════════


def test_license_file_template_has_no_legacy_inputs(client, app):
    _login(client)
    res = client.get("/admin/radius/license-file")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Legacy inputs / disclosure / activation card — all gone.
    assert 'name="shared_secret"' not in html
    assert 'name="server_fingerprint"' not in html
    assert 'name="sync_interval_seconds"' not in html
    assert "data-license-advanced" not in html
    assert "hub-disclosure" not in html
    assert "كود التفعيل" not in html
    assert "تفعيل الجسر — ربط تلقائي" not in html
    assert "إعدادات متقدّمة" not in html
    assert "(legacy)" not in html
    assert "سر الربط (legacy)" not in html
    # No HMAC mention anywhere.
    assert "HMAC" not in html


def test_license_file_template_has_required_minimal_inputs(client, app):
    _login(client)
    html = client.get("/admin/radius/license-file").get_data(as_text=True)
    # Required minimal inputs.
    assert 'name="base_url"' in html
    assert 'name="license_key"' in html
    assert 'name="enabled"' in html
    # Sync toggles are visible (not behind disclosure).
    assert 'name="runtime_contract_sync"' in html
    assert 'name="identity_sync_enabled"' in html
    assert 'name="identity_sync_on_login"' in html
    assert 'name="worker_enabled"' in html
    # The single-credential message.
    assert "هذا المفتاح هو سرّ الربط الوحيد" in html


# ════════════════════════════════════════════════════════════════════
# (4) Setup POST no longer accepts shared_secret / fingerprint
# ════════════════════════════════════════════════════════════════════


def test_setup_post_ignores_shared_secret_field(client, app):
    """Even if a stale POST submits shared_secret, the handler ignores
    it — nothing gets stored to ``license_admin_bridge.shared_secret``."""
    from app.radius.db.repos import tenants_repo

    _login(client)
    # Pull the CSRF token from a session.
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]

    res = client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "lic_purged_aaaa",
            "enabled": "1",
            # Legacy fields submitted in a stale form — must be IGNORED.
            "shared_secret": "should-not-be-stored",
            "server_fingerprint": "should-not-be-stored",
            "sync_interval_seconds": "120",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    # license_key was stored.
    assert tenants_repo.get_setting(1, "license_admin_bridge.license_key", "") == "lic_purged_aaaa"
    # shared_secret / server_fingerprint / sync_interval_seconds were NOT.
    assert tenants_repo.get_setting(
        1, "license_admin_bridge.shared_secret", "_NONE_") in ("", "_NONE_")
    assert tenants_repo.get_setting(
        1, "license_admin_bridge.server_fingerprint", "_NONE_") in ("", "_NONE_")
    assert tenants_repo.get_setting(
        1, "license_admin_bridge.sync_interval_seconds", "_NONE_") in ("", "_NONE_")
