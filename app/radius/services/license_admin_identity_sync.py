"""Identity sync from radius-module-admin into local RADIUS admins.

The license panel is the source of truth for customer admin users. This module
never accepts or stores plaintext passwords; it only applies password hashes
and version metadata delivered over the signed HTTPS admin bridge.
"""
from __future__ import annotations

from typing import Any

from app.radius.core.types import Admin
from app.radius.db.repos import admins_repo
from app.radius.services.admin_panel_client import (
    AdminBridgeConfig,
    AdminPanelClient,
    LicenseAdminSnapshotStore,
    sanitize_bridge_payload,
)


class LicenseAdminIdentitySyncService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
        store: LicenseAdminSnapshotStore | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.store = store or LicenseAdminSnapshotStore()
        self.admin_client = admin_client or AdminPanelClient(config=self.config, store=self.store)

    def sync_once(self, *, tenant_id: int = 1, disable_missing: bool = False) -> dict[str, Any]:
        result = self.admin_client.fetch_identity_sync(tenant_id=tenant_id)
        if not result.get("ok"):
            return {
                "ok": False,
                "status": result.get("status") or "unavailable",
                "error": sanitize_bridge_payload(result.get("error") or {}),
            }
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        problems = validate_identity_payload(payload)
        if problems:
            return {
                "ok": False,
                "status": "invalid_payload",
                "error": {"code": "invalid_payload", "problems": problems},
            }

        synced: list[dict[str, Any]] = []
        active_external_ids: set[str] = set()
        for user in payload.get("users") or []:
            external_id = str(user.get("external_user_id"))
            active_external_ids.add(external_id)
            admin = admins_repo.upsert_license_admin_user(
                external_user_id=external_id,
                username=str(user.get("username") or ""),
                password_hash=str(user.get("password_hash") or ""),
                password_hash_scheme=str(user.get("password_hash_scheme") or "werkzeug"),
                password_version=int(user.get("password_version") or 0),
                full_name=str(user.get("full_name") or ""),
                email=str(user.get("email") or ""),
                role_key=str(user.get("role_key") or "viewer"),
                active=bool(user.get("active")),
                updated_at=str(user.get("updated_at") or ""),
            )
            synced.append({
                "admin_id": admin.id,
                "username": admin.username,
                "external_user_id": external_id,
                "active": admin.enabled,
                "password_version": admin.external_password_version,
            })
        disabled_missing = 0
        if disable_missing:
            disabled_missing = admins_repo.disable_missing_license_admin_users(active_external_ids)
        return {
            "ok": True,
            "status": "ok",
            "customer_id": payload.get("customer_id"),
            "license_key": payload.get("license_key"),
            "version": payload.get("version"),
            "synced_count": len(synced),
            "disabled_missing_count": disabled_missing,
            "users": synced,
        }

    def change_password_from_runtime(
        self,
        *,
        admin: Admin,
        new_password: str,
        tenant_id: int = 1,
    ) -> dict[str, Any]:
        if not admin.managed_by_license_admin or admin.external_identity_provider != "license_admin":
            return {
                "ok": False,
                "status": "local_account",
                "error": {"code": "local_account", "message": "admin is not managed by license admin"},
            }
        if len(str(new_password or "")) < 8:
            return {
                "ok": False,
                "status": "invalid_password",
                "error": {"code": "invalid_password", "message": "password must be at least 8 characters"},
            }
        result = self.admin_client.post_customer_user_password_change(
            external_user_id=admin.external_subject,
            username=admin.username,
            new_password=new_password,
        )
        if not result.get("ok"):
            return result
        sync_result = self.sync_once(tenant_id=tenant_id)
        return {
            "ok": bool(sync_result.get("ok")),
            "status": "updated" if sync_result.get("ok") else sync_result.get("status", "sync_failed"),
            "message": "تم تحديث كلمة المرور من لوحة التراخيص",
            "panel_response": result.get("response") or {},
            "sync": sync_result,
        }


def validate_identity_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if payload.get("ok") is not True:
        problems.append("ok must be true")
    users = payload.get("users")
    if not isinstance(users, list):
        return [*problems, "users must be a list"]
    for idx, user in enumerate(users):
        if not isinstance(user, dict):
            problems.append(f"users[{idx}] must be an object")
            continue
        if "password" in user or "plain_password" in user:
            problems.append(f"users[{idx}] contains plaintext password")
        if not user.get("external_user_id"):
            problems.append(f"users[{idx}].external_user_id is required")
        if not user.get("username"):
            problems.append(f"users[{idx}].username is required")
        if str(user.get("password_hash_scheme") or "").lower() != "werkzeug":
            problems.append(f"users[{idx}].password_hash_scheme must be werkzeug")
        if not str(user.get("password_hash") or "").startswith(("scrypt:", "pbkdf2:", "argon2:")):
            problems.append(f"users[{idx}].password_hash must be a supported Werkzeug hash")
    return problems
