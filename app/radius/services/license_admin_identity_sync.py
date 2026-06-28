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
        super_overrides = apply_super_admin_overrides(payload.get("admin_super_overrides"))
        # Declarative panel-admin management from the licensing owner (create /
        # set-permissions / deactivate), keyed by username and applied idempotently.
        admin_directives = apply_admin_directives(payload.get("admin_directives"))
        # The licensing panel's explicit OWNER designation rides identity-sync
        # too (same ``owner_admins`` key/source as the runtime contract). Persist
        # a non-empty set as authoritative; absent/empty leaves the min-id
        # fallback intact (never strips the existing owner).
        from app.radius.services.license_admin_runtime_sync import (
            apply_owner_admins_designation,
        )
        owner_admins = apply_owner_admins_designation(payload.get("owner_admins"))
        return {
            "ok": True,
            "status": "ok",
            "customer_id": payload.get("customer_id"),
            "license_key": payload.get("license_key"),
            "version": payload.get("version"),
            "synced_count": len(synced),
            "disabled_missing_count": disabled_missing,
            "super_overrides": super_overrides,
            "admin_directives": admin_directives,
            "owner_admins": owner_admins,
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


def apply_super_admin_overrides(overrides: Any) -> dict[str, int]:
    """Enforce the panel's super-admin decisions on local admins.

    ``overrides`` is the ``admin_super_overrides`` list returned by the panel in
    the identity-sync response, each item shaped like
    ``{"radius_admin_id": 5, "username": "owner", "is_super_admin": true}``.

    For each entry we match the local admin by ``radius_admin_id`` first then
    ``username`` and set ONLY the is_super_admin flag (idempotent; password,
    identity provider, role and enabled state are never touched).
    """
    summary = {"changed": 0, "unchanged": 0, "not_found": 0}
    if not isinstance(overrides, list):
        return summary
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        outcome = admins_repo.apply_super_admin_override(
            radius_admin_id=entry.get("radius_admin_id"),
            username=str(entry.get("username") or ""),
            is_super_admin=bool(entry.get("is_super_admin")),
        )
        if outcome in summary:
            summary[outcome] += 1
    return summary


def apply_admin_directives(directives: Any) -> dict[str, int]:
    """Apply the licensing owner's declarative panel-admin management.

    ``directives`` is the ``admin_directives`` list in the identity-sync payload,
    each item shaped like
    ``{"op": "upsert"|"deactivate", "username": "...", "role_key": "operator",
       "active": true, "password_hash": "scrypt:...", "must_change_password": true}``.

    Each is applied idempotently via ``admins_repo.apply_managed_admin_directive``:
    create (with the one-time werkzeug hash + force-change-on-first-login), set
    permissions (role), or deactivate (recoverable). The owner-protection and
    last-admin guards are enforced in the repo. A re-applied directive is a no-op.

    SECURITY: a directive must NEVER carry a plaintext password — such an entry
    is dropped, never applied.
    """
    summary = {
        "created": 0, "updated": 0, "unchanged": 0, "deactivated": 0,
        "skipped_identity_managed": 0, "skipped_owner": 0, "skipped_last_admin": 0,
        "skipped_no_password": 0, "invalid": 0, "rejected_plaintext": 0,
    }
    if not isinstance(directives, list):
        return summary
    for entry in directives:
        if not isinstance(entry, dict):
            summary["invalid"] += 1
            continue
        if "password" in entry or "plain_password" in entry:
            summary["rejected_plaintext"] += 1     # never trust a plaintext secret
            continue
        outcome = admins_repo.apply_managed_admin_directive(
            op=str(entry.get("op") or "upsert"),
            username=str(entry.get("username") or ""),
            role_key=str(entry.get("role_key") or "viewer"),
            active=bool(entry.get("active", True)),
            password_hash=str(entry.get("password_hash") or ""),
            password_hash_scheme=str(entry.get("password_hash_scheme") or ""),
            must_change_password=bool(entry.get("must_change_password")),
        )
        if outcome in summary:
            summary[outcome] += 1
    return summary


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
