"""PlansService — إدارة الباقات/العروض."""
from __future__ import annotations

from typing import Sequence

from ..core.constants import AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE, AUDIT_ACTION_UPDATE, PLAN_TYPES
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
        try:                                  # لقطة «قبل» لعرض الفرق في السجلّ
            existing = self._adapter.get_profile(plan.id)
        except Exception:  # noqa: BLE001
            existing = None
        saved = self._adapter.upsert_profile(plan)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="plan", target_id=str(saved.id),
                           payload={"name": saved.name},
                           before=_plan_snapshot(existing),
                           after=_plan_snapshot(saved))
        # «لو عدّلت العرض إنه يوم الجمعة غير متاح، فورًا الي مش مطابق ينطرد»:
        # إعادة فحص الجلسات الحيّة لمستخدمي هذا العرض ضد قواعده الجديدة
        # (أيام/ساعات، كوتا، حدّ أجهزة…) وطرد المخالف الآن — محصّن ولا يُبطئ
        # الحفظ (خيط خلفيّ).
        try:
            from .policy_reconciler import reconcile_active_sessions_against_policy
            reconcile_active_sessions_against_policy(
                int(getattr(saved, "tenant_id", 0) or 1),
                plan_id=int(saved.id), reason="plan_update")
        except Exception:  # noqa: BLE001 — الإنفاذ لا يكسر الحفظ أبدًا
            pass
        return saved

    def delete(self, *, actor: str, plan_id: int) -> None:
        self._adapter.delete_profile(plan_id)
        self._audit.record(actor=actor, action=AUDIT_ACTION_ARCHIVE,
                           target_type="plan", target_id=str(plan_id),
                           payload={"mode": "soft_delete"})


def _plan_snapshot(plan) -> dict:
    """لقطة مقروءة لحقول العرض ذات المعنى — تُخزَّن في before/after فيَعرض
    سجلّ التغييرات «الحقل: من X إلى Y» عند تعديل عرض."""
    if plan is None:
        return {}
    g = lambda a, d=None: getattr(plan, a, d)
    return {
        "name": (g("name", "") or "").strip(),
        "speed_down_kbps": int(g("speed_down_kbps", 0) or 0),
        "speed_up_kbps": int(g("speed_up_kbps", 0) or 0),
        "quota_total_mb": int(g("quota_total_mb", 0) or 0),
        "duration_minutes": int(g("duration_minutes", 0) or 0),
        "validity_days": int(g("validity_days", 0) or 0),
        "price": g("price"),
        "max_daily_minutes": int(g("max_daily_minutes", 0) or 0),
        "device_limit": g("device_limit"),
    }


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
