from __future__ import annotations

import os

import pytest
from werkzeug.security import generate_password_hash

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


class MockTransport:
    def __init__(self, response=None):
        self.response = response or {}
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class PasswordChangeTransport:
    def __init__(self):
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        url = kwargs.get("url") or ""
        if url.endswith("/customer-users/password-change"):
            return {"ok": True, "status": "updated", "password_version": 5}
        if url.endswith("/identity-sync"):
            return _identity_payload(password="RuntimeSecret123!", version=5)
        return {"ok": False, "status": "unexpected"}


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "identity_sync.db"))
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
        enabled=True,
        base_url="https://license-panel.test",
        license_key="HBR-2026-AAAA-BBBB-CCCC",
        shared_secret="shared-secret-value-at-least-32",
        timeout_seconds=1.0,
        retry_count=0,
    )


def _identity_payload(*, active=True, password="Secret123!", version=4):
    return {
        "ok": True,
        "customer_id": 12,
        "license_key": "HBR-2026-AAAA-BBBB-CCCC",
        "version": version,
        "users": [
            {
                "external_user_id": 7,
                "username": "customer-admin",
                "email": "owner@example.com",
                "full_name": "Owner",
                "role_key": "owner",
                "active": active,
                "password_hash": generate_password_hash(password),
                "password_hash_scheme": "werkzeug",
                "password_version": version,
                "updated_at": "2026-05-29T12:00:00Z",
            }
        ],
    }


def test_verify_password_accepts_werkzeug_hash(app_db):
    from app.radius.db.repos.admins_repo import verify_password

    stored = generate_password_hash("Secret123!")

    assert verify_password("Secret123!", stored) is True
    assert verify_password("wrong", stored) is False


def test_identity_sync_creates_local_managed_admin_and_login_works(client, app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient, LicenseAdminSnapshotStore
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    store = LicenseAdminSnapshotStore()
    admin_client = AdminPanelClient(config=_config(), transport=MockTransport(_identity_payload()), store=store)
    result = LicenseAdminIdentitySyncService(config=_config(), admin_client=admin_client, store=store).sync_once(tenant_id=1)

    assert result["ok"] is True
    assert result["synced_count"] == 1

    login = client.post("/admin/radius/login", data={"username": "customer-admin", "password": "Secret123!"})
    assert login.status_code in {302, 303}

    from app.radius.db.repos import admins_repo

    admin = admins_repo.get_by_username("customer-admin")
    assert admin.managed_by_license_admin is True
    assert admin.external_password_hash_scheme == "werkzeug"
    assert admin.external_password_version == 4


def test_disabled_customer_user_cannot_login_after_sync(client, app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient, LicenseAdminSnapshotStore
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    store = LicenseAdminSnapshotStore()
    admin_client = AdminPanelClient(config=_config(), transport=MockTransport(_identity_payload(active=False)), store=store)
    LicenseAdminIdentitySyncService(config=_config(), admin_client=admin_client, store=store).sync_once(tenant_id=1)

    login = client.post("/admin/radius/login", data={"username": "customer-admin", "password": "Secret123!"})

    assert login.status_code == 401


def test_managed_admin_password_change_is_blocked(app_db):
    from app.radius.db.repos import admins_repo

    admins_repo.upsert_license_admin_user(
        external_user_id=7,
        username="customer-admin",
        password_hash=generate_password_hash("Secret123!"),
        password_hash_scheme="werkzeug",
        password_version=1,
        role_key="owner",
        active=True,
    )
    admin = admins_repo.get_by_username("customer-admin")

    with pytest.raises(ValueError, match="لوحة التراخيص"):
        admins_repo.update_admin(admin.id, password="LocalChange123!")


def test_runtime_password_change_posts_to_license_panel_then_syncs_hash(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminPanelClient, LicenseAdminSnapshotStore
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    admins_repo.upsert_license_admin_user(
        external_user_id=7,
        username="customer-admin",
        password_hash=generate_password_hash("Secret123!"),
        password_hash_scheme="werkzeug",
        password_version=4,
        role_key="owner",
        active=True,
    )
    admin = admins_repo.get_by_username("customer-admin")
    transport = PasswordChangeTransport()
    store = LicenseAdminSnapshotStore()
    admin_client = AdminPanelClient(config=_config(), transport=transport, store=store)

    result = LicenseAdminIdentitySyncService(config=_config(), admin_client=admin_client, store=store).change_password_from_runtime(
        admin=admin,
        new_password="RuntimeSecret123!",
        tenant_id=1,
    )
    updated = admins_repo.get_by_username("customer-admin")

    assert result["ok"] is True
    assert result["status"] == "updated"
    assert [call["url"].split("/")[-1] for call in transport.calls] == ["password-change", "identity-sync"]
    assert transport.calls[0]["json_body"]["external_user_id"] == "7"
    assert transport.calls[0]["json_body"]["new_password"] == "RuntimeSecret123!"
    assert transport.calls[0]["json_body"]["signature"]
    assert admins_repo.verify_password("RuntimeSecret123!", updated.password_hash) is True
    assert updated.external_password_version == 5


def test_identity_sync_requires_https_before_password_hash_transfer(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient

    config = AdminBridgeConfig(
        enabled=True,
        base_url="http://license-panel.test",
        license_key="HBR-2026-AAAA-BBBB-CCCC",
        shared_secret="shared-secret-value-at-least-32",
        timeout_seconds=1.0,
        retry_count=0,
    )
    transport = MockTransport(response=_identity_payload())

    result = AdminPanelClient(config=config, transport=transport).fetch_identity_sync(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "https_required"
    assert transport.calls == []


def test_identity_sync_remote_rejection_is_not_treated_as_payload_bug(app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient

    transport = MockTransport(response={"ok": False, "status": "denied", "message": "signature rejected"})

    result = AdminPanelClient(config=_config(), transport=transport).fetch_identity_sync(tenant_id=1)

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert result["error"]["status"] == "denied"
    assert result["status"] != "invalid_payload"


def test_runtime_password_change_requires_https_client_side(app_db):
    from app.radius.db.repos import admins_repo
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    admins_repo.upsert_license_admin_user(
        external_user_id=7,
        username="customer-admin",
        password_hash=generate_password_hash("Secret123!"),
        password_hash_scheme="werkzeug",
        password_version=4,
        role_key="owner",
        active=True,
    )
    admin = admins_repo.get_by_username("customer-admin")
    config = AdminBridgeConfig(
        enabled=True,
        base_url="http://license-panel.test",
        license_key="HBR-2026-AAAA-BBBB-CCCC",
        shared_secret="shared-secret-value-at-least-32",
        timeout_seconds=1.0,
        retry_count=0,
    )
    result = LicenseAdminIdentitySyncService(config=config, admin_client=AdminPanelClient(config=config, transport=MockTransport())).change_password_from_runtime(
        admin=admin,
        new_password="RuntimeSecret123!",
        tenant_id=1,
    )

    assert result["ok"] is False
    assert result["status"] == "https_required"


def test_account_page_renders_for_managed_admin(client, app_db):
    from app.radius.db.repos import admins_repo

    admins_repo.upsert_license_admin_user(
        external_user_id=7,
        username="customer-admin",
        password_hash=generate_password_hash("Secret123!"),
        password_hash_scheme="werkzeug",
        password_version=4,
        role_key="owner",
        active=True,
    )
    admin = admins_repo.get_by_username("customer-admin")
    with client.session_transaction() as sess:
        sess["admin_id"] = admin.id
        sess["admin_user"] = admin.username
        sess["admin_name"] = admin.full_name or admin.username
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "account-csrf"

    res = client.get("/admin/radius/account")

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "customer-admin" in body
    assert "Bidirectional password change" in body


def test_identity_sync_rejects_plaintext_password(app_db):
    from app.radius.services.license_admin_identity_sync import validate_identity_payload

    payload = _identity_payload()
    payload["users"][0]["password"] = "Secret123!"

    assert "contains plaintext password" in " ".join(validate_identity_payload(payload))


def test_disabled_service_in_contract_blocks_local_cards_action(app_db):
    from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore, SNAPSHOT_CAPACITY
    from app.radius.services.license_admin_capacity import CapacityEnforcementService

    store = LicenseAdminSnapshotStore()
    store.save(
        tenant_id=1,
        snapshot_type=SNAPSHOT_CAPACITY,
        normalized_status="active",
        source_url="test",
        payload={
            "contract": {
                "license": {"active": True, "status": "active"},
                "services": {"cards": {"enabled": False, "status": "disabled"}},
                "limits": {},
                "features": {"cards": {"state": "enabled"}},
            }
        },
    )

    decision = CapacityEnforcementService(store=store).check_cards_generate(tenant_id=1, requested_count=1)

    assert decision.allowed is False
    assert decision.code == "service_not_enabled"


def test_identity_sync_endpoint_is_available_when_bridge_disabled(client):
    res = client.post("/api/v1/system/admin-bridge/identity-sync", json={}, headers=AUTH)

    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["ok"] is False
    assert body["data"]["status"] == "disabled"
