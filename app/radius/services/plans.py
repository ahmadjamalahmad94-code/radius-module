"""PlansService — إدارة الباقات/العروض."""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ..core.constants import AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE, AUDIT_ACTION_UPDATE, PLAN_TYPES
from ..core.errors import RadiusValidationError
from ..core.types import AccessPlan
from ..integration.adapter import RadiusAdapter
from .operations import validate_service_scope
from .audit import RadiusAuditService

# لاحقة اسم النسخة — تُميّز العرض المنسوخ بوضوح في القائمة.
CLONE_NAME_SUFFIX = " - نسخة"


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

    def clone(self, *, actor: str, plan_id: int) -> AccessPlan:
        """Deep-copy an existing plan into a brand-new, editable plan.

        Every plan field is copied generically via ``dataclasses.replace`` —
        because ``AccessPlan`` mirrors the full ``access_plans`` schema, no
        column is ever missed (even as the schema grows). Only identity and
        lifecycle fields are reset so the copy is a fresh, distinct row:
          - ``id`` → None so the adapter INSERTs a new row.
          - ``created_at`` / ``updated_at`` → None so timestamps are fresh.
          - soft-delete fields → cleared (a copy is never born archived).
        The name is suffixed «- نسخة» (deduplicated against existing names,
        since ``access_plans`` has a UNIQUE (tenant_id, name) index).
        Logged as a normal plan-create audit event with a ``cloned_from`` marker.
        """
        src = self._adapter.get_profile(plan_id)  # 404s if missing / out of tenant scope
        new_name = self._unique_clone_name(src.name)
        dup = replace(
            src,
            id=None,
            name=new_name,
            created_at=None,
            updated_at=None,
            deleted_at=None,
            deleted_by="",
            delete_reason="",
        )
        _validate(dup)
        saved = self._adapter.upsert_profile(dup)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="plan", target_id=str(saved.id),
                           payload={"name": saved.name, "type": saved.plan_type,
                                    "op": "clone", "cloned_from": plan_id})
        return saved

    def _unique_clone_name(self, source_name: str) -> str:
        """«الاسم - نسخة»، ثم «… - نسخة 2/3…» عند التعارض — يحترم قيد التفرّد."""
        base = (source_name or "عرض").strip()
        existing = {(p.name or "").strip() for p in self.list(limit=500)}
        candidate = f"{base}{CLONE_NAME_SUFFIX}"
        if candidate not in existing:
            return candidate
        n = 2
        while f"{candidate} {n}" in existing:
            n += 1
        return f"{candidate} {n}"

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
