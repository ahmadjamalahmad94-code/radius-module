"""Security guards for the admin bridge (SEC C1 + C2).

C1 — the identity-sync response can carry privilege-ESCALATION directives
    (admin_super_overrides / admin_directives / owner_admins) that mint a
    super-admin or reassign ownership. They are fail-closed behind TWO layers:
      (1) operator opt-in (HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION), AND
      (2) a valid HMAC `_bridge_sig` on the response, keyed by our own license
          key — so a rogue/repointed panel that doesn't know the key can't
          forge escalation even if the operator opted in.
    Ordinary user metadata sync is unaffected.
C2 — saving the bridge base_url (license_file_config) is super-only, not
    "api.use" — a non-owner with api.use could otherwise repoint the bridge.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

_LICENSE_KEY = "HBR-2026-AAAA-BBBB-CCCC"


def _sign(payload: dict, key: str = _LICENSE_KEY) -> str:
    """Reproduce the panel's canonical signing (see admin license_signing)."""
    body = {k: v for k, v in payload.items() if k != "_bridge_sig"}
    msg = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hmac.new(key.strip().upper().encode("utf-8"),
                    msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _sign_payload(payload: dict, key: str = _LICENSE_KEY) -> dict:
    payload["_bridge_sig"] = _sign(payload, key)
    return payload


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
        license_key=_LICENSE_KEY, timeout_seconds=5, retry_count=0)


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


def _run_sync(app, payload=None):
    if payload is None:
        payload = _escalation_payload()
    with app.app_context():
        from app.radius.services.admin_panel_client import (
            AdminPanelClient, LicenseAdminSnapshotStore)
        from app.radius.services.license_admin_identity_sync import (
            LicenseAdminIdentitySyncService)
        store = LicenseAdminSnapshotStore()
        ac = AdminPanelClient(config=_config(),
                              transport=_MockTransport(payload),
                              store=store)
        return LicenseAdminIdentitySyncService(
            config=_config(), admin_client=ac, store=store).sync_once(tenant_id=1)


def _assert_blocked(app, result):
    assert result["ok"] is True
    assert result["synced_count"] == 1              # ordinary sync still works
    assert result["super_overrides"] == {"blocked": True}
    assert result["admin_directives"] == {"blocked": True}
    assert result["owner_admins"] == {"blocked": True}
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_by_username("attacker") is None
        victim = admins_repo.get_by_username("victim-admin")
        assert victim is not None and not getattr(victim, "is_super_admin", False)


def test_escalation_blocked_by_default(app):
    # No opt-in, signed or not → blocked.
    _assert_blocked(app, _run_sync(app, _sign_payload(_escalation_payload())))


def test_escalation_blocked_when_opted_in_but_unsigned(app, monkeypatch):
    # Opt-in ON but the response carries NO signature → still blocked. This is
    # the rogue/repointed-panel case: it can't produce a valid `_bridge_sig`.
    monkeypatch.setenv("HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION", "1")
    _assert_blocked(app, _run_sync(app, _escalation_payload()))


def test_escalation_blocked_when_opted_in_but_signature_wrong(app, monkeypatch):
    # Opt-in ON, but signed with the WRONG key (attacker doesn't know ours).
    monkeypatch.setenv("HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION", "1")
    forged = _sign_payload(_escalation_payload(), key="HBR-9999-WRONG-KEY-XXXX")
    _assert_blocked(app, _run_sync(app, forged))


def test_escalation_applied_when_opted_in_and_signed(app, monkeypatch):
    # Both layers satisfied: operator opt-in AND a signature we can reproduce
    # with our own license key → directives applied.
    monkeypatch.setenv("HOBERADIUS_BRIDGE_TRUST_ADMIN_ESCALATION", "1")
    result = _run_sync(app, _sign_payload(_escalation_payload()))
    assert result["ok"] is True
    assert result["super_overrides"] != {"blocked": True}
    assert result["admin_directives"] != {"blocked": True}


def test_c2_base_url_config_is_super_only(app):
    # license_file_config (saves the bridge base_url) is gated __super__.
    from app.radius.routes.blueprint import _PERM_GUARDED, _PERM_SUPER
    assert _PERM_GUARDED.get("license_file_config") == _PERM_SUPER
