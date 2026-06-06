"""Admin-inventory reporter for super-admin enforcement (producer side).

Sends the local admin roster to the license panel so the panel can decide which
admins may be super. The panel returns its decisions as ``admin_super_overrides``
in the identity-sync response (consumed by license_admin_identity_sync).

SECURITY: only non-secret identity fields are sent — never a password hash.
"""
from __future__ import annotations

from typing import Any

from app.radius.db.repos import admins_repo
from app.radius.services.admin_panel_client import (
    AdminBridgeConfig,
    AdminPanelClient,
)

# Exactly the fields the panel contract expects (no password material).
_REPORT_FIELDS = (
    "id",
    "username",
    "role",
    "is_super_admin",
    "enabled",
    "managed_by_license_admin",
    "external_identity_provider",
)


def build_admin_inventory() -> list[dict[str, Any]]:
    """Return the local admin roster as plain dicts for the report payload."""
    role_names: dict[int, str] = {}
    for role in admins_repo.list_roles(include_deleted=True):
        role_names[role.id] = role.name
    inventory: list[dict[str, Any]] = []
    for admin in admins_repo.list_admins():
        inventory.append({
            "id": admin.id,
            "username": admin.username,
            "role": role_names.get(admin.role_id, "") if admin.role_id else "",
            "is_super_admin": bool(admin.is_super_admin),
            "enabled": bool(admin.enabled),
            "managed_by_license_admin": bool(admin.managed_by_license_admin),
            "external_identity_provider": admin.external_identity_provider or "",
        })
    return inventory


class LicenseAdminInventoryReportService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    def report_once(self, *, tenant_id: int = 1) -> dict[str, Any]:
        admins = build_admin_inventory()
        result = self.admin_client.post_admins_report(admins=admins)
        if not result.get("ok"):
            return {
                "ok": False,
                "status": result.get("status") or "unavailable",
                "error": result.get("error") or {},
                "reported_count": len(admins),
            }
        return {
            "ok": True,
            "status": result.get("status") or "ok",
            "reported_count": len(admins),
            "response": result.get("response") or {},
        }
