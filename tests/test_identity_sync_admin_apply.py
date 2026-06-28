"""Panel-side apply of full admin management from the licensing panel.

feat/identity-sync-admin-apply extends the identity-sync consumer so the
licensing customer file can FULLY manage this panel's admins:

  • CREATE with «force change on first login» — the flag rides identity-sync,
    is stored idempotently (re-applying the same contract is a no-op), and the
    login gate diverts the admin to the change-password page until satisfied.
  • DEACTIVATE with lockout-safety GUARDS — a sync may never disable a
    designated OWNER or the LAST enabled admin (defense in depth mirroring the
    licensing-side guards).
  • clearing the flag after a successful password change.
"""
from __future__ import annotations

import os

import pytest
from werkzeug.security import generate_password_hash

from app.radius.db.connection import reset_for_tests


class MockTransport:
    def __init__(self, response=None):
        self.response = response or {}

    def request_json(self, **kwargs):
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "admin_apply.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.repos import admins_repo
        admins_repo.ensure_default_roles()
        yield app
    reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True, base_url="https://license-panel.test",
        license_key="HBR-2026-AAAA-BBBB-CCCC", timeout_seconds=1.0, retry_count=0,
    )


def _user(uid, username, *, role="admin", active=True, email="",
          version=1, force=False, password="Secret123!"):
    return {
        "external_user_id": uid, "username": username, "email": email,
        "full_name": "", "role_key": role, "active": active,
        "password_hash": generate_password_hash(password),
        "password_hash_scheme": "werkzeug", "password_version": version,
        "force_password_change": force, "updated_at": "2026-06-28T12:00:00Z",
    }


def _payload(users):
    return {"ok": True, "customer_id": 12, "license_key": "HBR-2026-AAAA-BBBB-CCCC",
            "version": 9, "users": users}


def _sync(payload):
    from app.radius.services.admin_panel_client import AdminPanelClient, LicenseAdminSnapshotStore
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService
    store = LicenseAdminSnapshotStore()
    ac = AdminPanelClient(config=_config(), transport=MockTransport(payload), store=store)
    return LicenseAdminIdentitySyncService(config=_config(), admin_client=ac, store=store).sync_once(tenant_id=1)


# ── CREATE stores force_password_change; re-apply is idempotent ───────────────
def test_force_password_change_stored_and_idempotent(app_db):
    from app.radius.db.repos import admins_repo
    _sync(_payload([_user(7, "fpc-admin", force=True)]))
    a = admins_repo.get_by_username("fpc-admin")
    assert a.force_password_change is True

    # The admin satisfies the requirement (changes password) → flag cleared.
    admins_repo.clear_force_password_change(a.id)
    assert admins_repo.get_by_username("fpc-admin").force_password_change is False

    # Re-applying the SAME contract (force=True) is authoritative → re-raises it.
    _sync(_payload([_user(7, "fpc-admin", force=True)]))
    assert admins_repo.get_by_username("fpc-admin").force_password_change is True

    # A contract with force=False clears it (no re-raise loop after self-change).
    _sync(_payload([_user(7, "fpc-admin", force=False)]))
    assert admins_repo.get_by_username("fpc-admin").force_password_change is False


# ── GUARD: a sync may not disable the LAST enabled admin ──────────────────────
def test_guard_blocks_disabling_last_admin(app_db):
    from app.radius.db.repos import admins_repo
    _sync(_payload([_user(7, "solo", active=True)]))
    assert admins_repo.count_enabled_admins() == 1

    result = _sync(_payload([_user(7, "solo", active=False)]))
    a = admins_repo.get_by_username("solo")
    assert a.enabled is True                                   # kept enabled
    assert any(g["reason"] == "last_admin" for g in result["guarded"])


# ── GUARD: a sync may not disable a DESIGNATED OWNER ──────────────────────────
def test_guard_blocks_disabling_designated_owner(app_db):
    from app.radius.db.repos import admins_repo
    # Two admins so "last_admin" is not the reason; owner is designated.
    _sync(_payload([_user(7, "boss", role="owner", email="boss@x.com"),
                    _user(8, "second", role="admin")]))
    admins_repo.set_designated_owners(["boss"])

    result = _sync(_payload([_user(7, "boss", role="owner", email="boss@x.com", active=False),
                             _user(8, "second", role="admin", active=True)]))
    assert admins_repo.get_by_username("boss").enabled is True
    assert any(g["reason"] == "owner" for g in result["guarded"])


# ── A non-owner, non-last admin CAN be deactivated by sync ────────────────────
def test_non_protected_admin_is_deactivated(app_db):
    from app.radius.db.repos import admins_repo
    _sync(_payload([_user(7, "keeper", role="owner"),
                    _user(8, "extra", role="admin", active=True)]))
    _sync(_payload([_user(7, "keeper", role="owner"),
                    _user(8, "extra", role="admin", active=False)]))
    assert admins_repo.get_by_username("extra").enabled is False
    assert admins_repo.get_by_username("keeper").enabled is True


# ── Login gate diverts a force-change admin to the account page ───────────────
def test_login_gate_diverts_force_change_admin(client, app_db):
    _sync(_payload([_user(7, "needchange", role="owner", force=True),
                    _user(8, "other", role="admin")]))
    login = client.post("/admin/radius/login",
                        data={"username": "needchange", "password": "Secret123!"})
    assert login.status_code in {302, 303}

    # A protected page redirects to the account (change-password) page.
    dash = client.get("/admin/radius/dashboard")
    assert dash.status_code in {302, 303}
    assert "/admin/radius/account" in dash.headers.get("Location", "")

    # The account page itself stays reachable (no redirect loop).
    acct = client.get("/admin/radius/account")
    assert acct.status_code == 200
