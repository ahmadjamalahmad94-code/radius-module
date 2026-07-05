"""Security guards for the admin bridge (SEC C1 + C2).

C1 — the identity-sync response is applied over HTTPS but is NOT signed. The
    privilege-ESCALATION directives (admin_super_overrides / admin_directives /
    owner_admins) that can mint a super-admin or reassign ownership are now
    fail-closed: applied only when HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION is
    explicitly enabled. Ordinary user metadata sync is unaffected.
C2 — saving the bridge base_url (license_file_config) is super-only, not
    "api.use" — a non-owner with api.use could otherwise repoint the bridge.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_secbridge_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION", raising=False)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


class _MockTransport:
    def __init__(self, payload):
        self._payload = payload

    def request_json(self, **kwargs):
        return self._payload


def _config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True, base_url="https://panel.example.test",
        license_key="HBR-2026-AAAA-BBBB-CCCC", timeout_seconds=5, retry_count=0)


def _escalation_payload():
    # A well-formed identity payload that ALSO carries escalation directives —
    # exactly what a rogue/MITM panel would return to mint a super-admin.
    return {
        "ok": True, "customer_id": 12, "license_key": "HBR-2026-AAAA-BBBB-CCCC",
        "version": 4,
        "users": [{
            "external_user_id": 7, "username": "victim-admin",
            "email": "v@example.com", "full_name": "V", "role_key": "viewer",
            "active": True, "password_hash": generate_password_hash("Secret123!"),
            "password_hash_scheme": "werkzeug", "password_version": 4,
            "updated_at": "2026-05-29T12:00:00Z",
        }],
        "admin_super_overrides": [
            {"username": "victim-admin", "is_super_admin": True}],
        "admin_directives": [
            {"username": "attacker", "action": "create",
             "is_super_admin": True, "password_hash": generate_password_hash("x"),
             "password_hash_scheme": "werkzeug"}],
        "owner_admins": ["attacker"],
    }


def _run_sync(app):
    with app.app_context():
        from app.radius.services.admin_panel_client import (
            AdminPanelClient, LicenseAdminSnapshotStore)
        from app.radius.services.license_admin_identity_sync import (
            LicenseAdminIdentitySyncService)
        store = LicenseAdminSnapshotStore()
        ac = AdminPanelClient(config=_config(),
                              transport=_MockTransport(_escalation_payload()),
                              store=store)
        return LicenseAdminIdentitySyncService(
            config=_config(), admin_client=ac, store=store).sync_once(tenant_id=1)


def test_escalation_blocked_by_default(app):
    result = _run_sync(app)
    assert result["ok"] is True
    assert result["synced_count"] == 1              # ordinary sync still works
    # Escalation directives were NOT applied…
    assert result["super_overrides"] == {"blocked": True}
    assert result["admin_directives"] == {"blocked": True}
    assert result["owner_admins"] == {"blocked": True}
    # …and no super-admin / attacker admin was minted.
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_by_username("attacker") is None
        victim = admins_repo.get_by_username("victim-admin")
        assert victim is not None and not getattr(victim, "is_super_admin", False)


def test_escalation_applied_when_operator_opts_in(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION", "1")
    result = _run_sync(app)
    assert result["ok"] is True
    # With the explicit opt-in, the directives are applied (not blocked).
    assert result["super_overrides"] != {"blocked": True}
    assert result["admin_directives"] != {"blocked": True}


def test_c2_base_url_config_is_super_only(app):
    # license_file_config (saves the bridge base_url) is gated __super__.
    from app.radius.routes.blueprint import _PERM_GUARDED, _PERM_SUPER
    assert _PERM_GUARDED.get("license_file_config") == _PERM_SUPER
