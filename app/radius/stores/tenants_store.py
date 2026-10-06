"""
TenantsStore — facade فوق tenants_repo (SQLite-backed).
الواجهة لم تتغيّر — يحافظ على توافق كل المستدعين.
"""
from __future__ import annotations

from threading import Lock
from typing import Optional

from ..core.tenant import (
    TENANT_STATUS_CLOSED, TENANT_STATUS_SUSPENDED, Tenant, TenantMembership,
)
from ..db.repos import tenants_repo

#: A member can never select (header / Basic) a tenant in one of these states.
UNSELECTABLE_TENANT_STATUSES = frozenset({TENANT_STATUS_SUSPENDED, TENANT_STATUS_CLOSED})


class TenantsStore:
    _inst: Optional["TenantsStore"] = None
    _inst_lock = Lock()

    @classmethod
    def instance(cls) -> "TenantsStore":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
        return cls._inst

    # Tenants
    def list(self) -> list[Tenant]:
        return tenants_repo.list_tenants()

    def get(self, tenant_id: int) -> Optional[Tenant]:
        return tenants_repo.get_tenant(tenant_id)

    def get_by_slug(self, slug: str) -> Optional[Tenant]:
        return tenants_repo.get_by_slug(slug)

    def create(self, t: Tenant) -> Tenant:
        return tenants_repo.create_tenant(t)

    def update(self, tenant_id: int, **changes) -> Optional[Tenant]:
        return tenants_repo.update_tenant(tenant_id, **changes)

    # Memberships
    def add_membership(self, m: TenantMembership) -> TenantMembership:
        return tenants_repo.add_membership(m)

    def memberships_for_admin(self, admin_id: int) -> list[TenantMembership]:
        return tenants_repo.memberships_for_admin(admin_id)

    def tenants_for_admin(self, admin_id: int) -> list[Tenant]:
        return tenants_repo.tenants_for_admin(admin_id)

    def selectable_tenants_for_admin(self, admin_id: int) -> list[Tenant]:
        """Tenants a NON-owner admin may select (owner decision 2026-10-06):
        an ACTIVE membership in a tenant that is not suspended / closed
        (``active`` and ``trial`` are usable). The platform owner / co-owners
        roam separately (``auth.owner.is_owner_like``) — never via this list."""
        return [t for t in tenants_repo.tenants_for_admin(admin_id)
                if (getattr(t, "status", "") or "active") not in UNSELECTABLE_TENANT_STATUSES]

    # Settings
    def get_setting(self, tenant_id: int, key: str, default: str = "") -> str:
        return tenants_repo.get_setting(tenant_id, key, default)

    def set_setting(self, tenant_id: int, key: str, value: str, *, by: int = 0) -> None:
        tenants_repo.set_setting(tenant_id, key, value, by=by)

    def list_settings(self, tenant_id: int) -> dict[str, str]:
        return tenants_repo.list_settings(tenant_id)
