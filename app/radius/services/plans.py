"""PlansService — إدارة الباقات/العروض."""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ..core.constants import AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE, AUDIT_ACTION_UPDATE, PLAN_TYPES
from ..core.errors import RadiusConflict, RadiusValidationError
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
        plan = _claim_plan_name(plan)
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
        plan = _claim_plan_name(plan)
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
        # 🔴 أرشفة باقةٍ عليها مشتركون كانت تنجح بصمت: يبقى المشترك على باقةٍ
        # مخفيّة (سعرها 0 في النوافذ، وتغيير الباقة يعامله «بلا باقة»). الآن
        # 409 مع العدد — انقلهم أوّلًا (نفس حارس سلّة المحذوفات).
        plan = self._adapter.get_profile(plan_id)
        refs = plan_references(int(getattr(plan, "tenant_id", 1) or 1), plan_id)
        if refs["total"]:
            raise RadiusConflict(
                "لا يمكن حذف الباقة: عليها %(s)d مشترك و%(b)d حزمة و%(c)d بطاقة — "
                "انقلهم إلى باقةٍ أخرى أوّلًا." % {
                    "s": refs["subscribers"], "b": refs["batches"], "c": refs["cards"]},
                details=refs)
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


def plan_references(tenant_id: int, plan_id: int) -> dict:
    """كم مشتركًا وحزمةً وبطاقةً (غير محذوفين) تعتمد على هذه الباقة."""
    from ..db.connection import db

    def _n(sql):
        try:
            return int(db().execute(sql, (int(tenant_id), int(plan_id))).fetchone()[0] or 0)
        except Exception:  # noqa: BLE001 — جدولٌ غائبٌ في نسخةٍ قديمة
            return 0
    s = _n("SELECT COUNT(*) FROM subscribers WHERE tenant_id=? AND plan_id=? "
           "AND deleted_at IS NULL AND COALESCE(user_type,'')!='card'")
    b = _n("SELECT COUNT(*) FROM card_batches WHERE tenant_id=? AND plan_id=? "
           "AND deleted_at IS NULL")
    c = _n("SELECT COUNT(*) FROM cards WHERE tenant_id=? AND plan_id=?")
    return {"subscribers": s, "batches": b, "cards": c, "total": s + b + c}


def _claim_plan_name(plan: AccessPlan) -> AccessPlan:
    """اسم الباقة: مطلوب، ومُفرَد بين الباقات **القائمة** (بلا تفريق حالة الأحرف
    أو المسافات الطرفيّة) ⇒ 422 عربيّ بدل 500 من فهرس التفرّد.

    الباقة **المؤرشفة** لا تحجز اسمها للأبد: فهرس التفرّد (tenant_id, name)
    يشمل المؤرشف، فيُعاد تسمية المؤرشفة «الاسم (مؤرشفة #id)» ليُعاد استخدام
    الاسم — وتبقى هي قابلةً للاستعادة باسمها الجديد."""
    from ..db.connection import db, transaction

    name = (plan.name or "").strip()
    if not name:
        raise RadiusValidationError("اسم الباقة مطلوب.")
    if name != plan.name:
        plan = replace(plan, name=name)
    tid = int(getattr(plan, "tenant_id", 1) or 1)
    rows = db().execute(
        "SELECT id, name, deleted_at FROM access_plans "
        "WHERE tenant_id = ? AND lower(trim(name)) = lower(?)", (tid, name),
    ).fetchall()
    others = [r for r in rows if plan.id is None or int(r["id"]) != int(plan.id)]
    if any(r["deleted_at"] is None for r in others):
        raise RadiusValidationError(f"اسم الباقة «{name}» مستخدم مسبقًا لباقة أخرى.")
    for r in others:
        if r["name"] == name:  # الفهرس حسّاس للحالة: المطابق حرفيًّا فقط يحجز
            with transaction() as conn:
                conn.execute(
                    "UPDATE access_plans SET name = ? WHERE tenant_id = ? AND id = ?",
                    (f"{name} (مؤرشفة #{int(r['id'])})", tid, int(r["id"])))
    return plan


_NON_NEGATIVE_PLAN_FIELDS = {
    "price": "السعر", "price_card": "سعر البطاقة", "price_bulk": "سعر الجملة",
    "duration_value": "المدّة", "duration_minutes": "المدّة بالدقائق",
    "validity_value": "الصلاحية", "validity_days": "أيّام الصلاحية",
    "quota_total_mb": "الكوتة الإجماليّة", "quota_daily_mb": "الكوتة اليوميّة",
    "quota_monthly_mb": "الكوتة الشهريّة",
    "daily_download_quota_mb": "كوتة التنزيل اليوميّة",
    "daily_upload_quota_mb": "كوتة الرفع اليوميّة",
    "daily_combined_quota_mb": "الكوتة اليوميّة الإجماليّة",
    "monthly_download_quota_mb": "كوتة التنزيل الشهريّة",
    "monthly_upload_quota_mb": "كوتة الرفع الشهريّة",
    "monthly_combined_quota_mb": "الكوتة الشهريّة الإجماليّة",
}


def _validate(plan: AccessPlan) -> None:
    if plan.plan_type not in PLAN_TYPES:
        raise RadiusValidationError(f"unknown plan_type: {plan.plan_type!r}")
    for field, label in _NON_NEGATIVE_PLAN_FIELDS.items():
        try:
            value = float(getattr(plan, field, 0) or 0)
        except (TypeError, ValueError):
            continue
        if value < 0:
            raise RadiusValidationError(f"{label} لا يمكن أن يكون سالبًا.")
    if plan.speed_down_kbps < 0 or plan.speed_up_kbps < 0:
        raise RadiusValidationError("speed must be >= 0")
    # 🔴 الصفرُ ليس «بلا حدّ» تلقائيًّا. ردٌّ بلا Mikrotik-Rate-Limit يجعل
    # الراوترَ يطبّق ملفَّه الافتراضيَّ (مفتوحًا عادةً)، فكان «نسيتُ
    # السرعة» و«أريدها مفتوحة» شيئًا واحدًا. المفتوحُ يُعلَّم صراحةً.
    if (plan.speed_down_kbps == 0 or plan.speed_up_kbps == 0)             and not plan.speed_unlimited:
        raise RadiusValidationError(
            "السرعة مطلوبة (تنزيل ورفع) — أو علّم «بلا حدّ للسرعة» صراحةً "
            "إن كانت الباقة مفتوحة.")
    if plan.concurrent_sessions < 1:
        raise RadiusValidationError("concurrent_sessions must be >= 1")
    validate_service_scope(plan.service_scope)
    if plan.max_loan_minutes < 0:
        raise RadiusValidationError("max_loan_minutes must be >= 0")


def get_plans_service() -> PlansService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return PlansService(get_radius_adapter(), audit=get_audit_service())
