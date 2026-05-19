"""PlansService — إدارة الباقات/العروض."""
from __future__ import annotations

from typing import Sequence

from ..core.constants import AUDIT_ACTION_CREATE, AUDIT_ACTION_UPDATE, AUDIT_ACTION_DELETE, PLAN_TYPES
from ..core.errors import RadiusValidationError
from ..core.types import AccessPlan
from ..integration.adapter import RadiusAdapter
from .operations import validate_service_scope
from .audit import RadiusAuditService


class PlansService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, limit: int = 200, offset: int = 0) -> Sequence[AccessPlan]:
        return self._adapter.list_profiles(limit=limit, offset=offset)

    def get(self, plan_id: int) -> AccessPlan:
        return self._adapter.get_profile(plan_id)

    def create(self, *, actor: str, plan: AccessPlan) -> AccessPlan:
        _validate(plan)
        saved = self._adapter.upsert_profile(plan)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="plan", target_id=str(saved.id),
                           payload={"name": saved.name, "type": saved.plan_type})
        return saved

    def update(self, *, actor: str, plan: AccessPlan) -> AccessPlan:
        if plan.id is None:
            raise RadiusValidationError("update requires id")
        _validate(plan)
        saved = self._adapter.upsert_profile(plan)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="plan", target_id=str(saved.id),
                           payload={"name": saved.name})
        return saved

    def delete(self, *, actor: str, plan_id: int) -> None:
        self._adapter.delete_profile(plan_id)
        self._audit.record(actor=actor, action=AUDIT_ACTION_DELETE,
                           target_type="plan", target_id=str(plan_id))


def _validate(plan: AccessPlan) -> None:
    if plan.plan_type not in PLAN_TYPES:
        raise RadiusValidationError(f"unknown plan_type: {plan.plan_type!r}")
    if plan.speed_down_kbps < 0 or plan.speed_up_kbps < 0:
        raise RadiusValidationError("speed must be >= 0")
    if plan.concurrent_sessions < 1:
        raise RadiusValidationError("concurrent_sessions must be >= 1")
    validate_service_scope(plan.service_scope)
    if plan.max_loan_minutes < 0:
        raise RadiusValidationError("max_loan_minutes must be >= 0")


def get_plans_service() -> PlansService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return PlansService(get_radius_adapter(), audit=get_audit_service())
