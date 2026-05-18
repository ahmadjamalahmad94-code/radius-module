"""TenantsService — إدارة الـ tenants."""
from __future__ import annotations

from typing import Optional

from ..core.constants import AUDIT_ACTION_CREATE, AUDIT_ACTION_DELETE, AUDIT_ACTION_UPDATE
from ..core.errors import RadiusValidationError
from ..core.tenant import TIER_LIMITS, Tenant, TENANT_TIER_STARTER
from ..stores.tenants_store import TenantsStore
from .audit import RadiusAuditService


class TenantsService:
    def __init__(self, audit: RadiusAuditService) -> None:
        self._store = TenantsStore.instance()
        self._audit = audit

    def list(self) -> list[Tenant]:
        return self._store.list()

    def get(self, tenant_id: int) -> Optional[Tenant]:
        return self._store.get(tenant_id)

    def create(self, *, actor: str, tenant: Tenant) -> Tenant:
        if not tenant.slug or not tenant.name:
            raise RadiusValidationError("slug + name مطلوبان")
        if tenant.plan_tier not in TIER_LIMITS:
            raise RadiusValidationError(f"plan_tier غير معروف: {tenant.plan_tier}")
        saved = self._store.create(tenant)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="tenant", target_id=str(saved.id),
                           payload={"slug": saved.slug, "tier": saved.plan_tier})
        return saved

    def update(self, *, actor: str, tenant_id: int, **changes) -> Optional[Tenant]:
        if "plan_tier" in changes and changes["plan_tier"] not in TIER_LIMITS:
            raise RadiusValidationError(f"plan_tier غير معروف")
        saved = self._store.update(tenant_id, **changes)
        if saved:
            self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                               target_type="tenant", target_id=str(tenant_id))
        return saved


def get_tenants_service() -> TenantsService:
    from .audit import get_audit_service
    return TenantsService(audit=get_audit_service())
