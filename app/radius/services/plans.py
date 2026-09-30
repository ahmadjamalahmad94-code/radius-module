"""PlansService — إدارة الباقات/العروض."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from ..core.constants import AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE, AUDIT_ACTION_UPDATE, PLAN_TYPES
from ..core.errors import RadiusConflict, RadiusValidationError
from ..core.types import AccessPlan
from ..db.repos.plans_repo import PLAN_NAME_MAX
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
        plan = _normalize(plan)
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
        # الاسم الناتج لا يتجاوز الحدّ (اسمٌ من 100 حرف + «- نسخة 12»).
        base = base[:max(1, PLAN_NAME_MAX - len(CLONE_NAME_SUFFIX) - 4)].rstrip()
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
            raise RadiusValidationError("تعديل الباقة يتطلّب معرّفها.")
        plan = _normalize(plan)
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
    if len(name) > PLAN_NAME_MAX:
        raise RadiusValidationError(
            f"اسم الباقة أطول من المسموح ({PLAN_NAME_MAX} حرفًا كحدّ أقصى).")
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
            suffix = f" (مؤرشفة #{int(r['id'])})"
            archived_name = name[:max(1, PLAN_NAME_MAX - len(suffix))].rstrip() + suffix
            with transaction() as conn:
                conn.execute(
                    "UPDATE access_plans SET name = ? WHERE tenant_id = ? AND id = ?",
                    (archived_name, tid, int(r["id"])))
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


# كلّ حقول الباقة العدديّة الصحيحة: غير سالبة وتحت سقفٍ عاقل (كانت 10^20
# و2^63 تُسقط SQLite بـ 500، وكانت الأجهزة/المهلات/الأولويّة/VLAN السالبة
# تُقبل). السقف الخاصّ لبعض الحقول في ``_INT_FIELD_MAX``.
PLAN_INT_MAX = 1_000_000_000
_INT_PLAN_FIELDS = {
    "max_daily_minutes": "الحدّ اليوميّ للدقائق",
    "max_weekly_minutes": "الحدّ الأسبوعيّ للدقائق",
    "max_monthly_minutes": "الحدّ الشهريّ للدقائق",
    "session_timeout_sec": "مهلة الجلسة",
    "idle_timeout_sec": "مهلة الخمول",
    "data_value": "حجم البيانات",
    "speed_up_kbps": "سرعة الرفع",
    "speed_down_kbps": "سرعة التنزيل",
    "burst_up_kbps": "سرعة الدفعة (رفع)",
    "burst_down_kbps": "سرعة الدفعة (تنزيل)",
    "burst_threshold_kbps": "عتبة الدفعة",
    "burst_time_sec": "زمن الدفعة",
    "cir_down_kbps": "السرعة المضمونة (تنزيل)",
    "cir_up_kbps": "السرعة المضمونة (رفع)",
    "concurrent_sessions": "عدد الجلسات المتزامنة",
    "vlan_id": "رقم VLAN",
    "allowed_devices_count": "عدد الأجهزة المسموحة",
    "priority": "الأولويّة",
    "max_consumption_times": "عدد مرّات الاستهلاك",
    "ticket_validity_days": "صلاحية التذكرة بالأيام",
    "working_hours_limit": "حدّ ساعات العمل",
    "max_loan_minutes": "الحدّ الأقصى لدقائق السلفة",
}
_INT_FIELD_MAX = {
    "vlan_id": 4094,
    "concurrent_sessions": 10_000,
    "allowed_devices_count": 10_000,
    "priority": 10,
    "session_timeout_sec": 10 * 365 * 86400,
    "idle_timeout_sec": 10 * 365 * 86400,
}
# حقول الساعات «HH:MM» (من/إلى). «24:00» مقبولة كنهاية يوم.
_HOUR_FIELDS = {
    "allowed_hours_from": "ساعة البداية",
    "allowed_hours_to": "ساعة النهاية",
    "offer_hours_from": "ساعات العرض — من",
    "offer_hours_to": "ساعات العرض — إلى",
}
_HOUR_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$|^24:00(?::00)?$")


def plan_field_label(field: str) -> str:
    """التسمية العربيّة لحقل باقة (لرسائل الخطأ) — الحقل الخام إن لم يُعرف."""
    return (_NON_NEGATIVE_PLAN_FIELDS.get(field) or _INT_PLAN_FIELDS.get(field)
            or _HOUR_FIELDS.get(field) or _OTHER_LABELS.get(field) or field)


_OTHER_LABELS = {
    "name": "اسم الباقة", "enabled": "تفعيل الباقة", "bind_mac": "ربط MAC",
    "bind_ip": "ربط IP", "force_mac_address": "فرض عنوان MAC",
    "auto_renew": "التجديد التلقائيّ", "prepaid": "الدفع المسبق",
    "speed_control_enabled": "التحكّم بالسرعة", "burst_enabled": "الدفعة (Burst)",
    "nightly_unlimited_enabled": "الليل المفتوح", "single_use_once": "استخدام مرّة واحدة",
    "hotspot_enabled": "هوت سبوت", "ppp_enabled": "PPP", "loan_enabled": "السلفة",
    "speed_override_allowed": "تجاوز السرعة", "speed_unlimited": "بلا حدّ للسرعة",
    "shared_single_session": "جلسة واحدة فعّالة", "bandwidth_id": "ملفّ السرعة",
    "pool_id": "مجمّع العناوين",
}


_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")


def _normalize(plan: AccessPlan) -> AccessPlan:
    """تطبيعٌ لا يغيّر المعنى: نطاق الخدمة بأحرفٍ صغيرة («HOTSPOT» كان يُخزَّن
    كما هو)، والأرقام العشريّة ‎-0.0 ⇒ 0."""
    changes = {}
    scope = (plan.service_scope or "").strip().lower()
    if scope != plan.service_scope:
        changes["service_scope"] = scope or "both"
    for f in ("price", "price_card", "price_bulk"):
        v = getattr(plan, f, 0)
        if isinstance(v, float) and v == 0 and str(v).startswith("-"):
            changes[f] = 0.0
    # الأولويّة: 0/فارغ/100 = الافتراضات القديمة (الـAPI والتطبيق) ⇒ 5. غير ذلك
    # خارج 1–10 يرفضه ``_validate`` (F04 N-L10).
    from ..db.repos.plans_repo import PRIORITY_DEFAULT, _LEGACY_PRIORITY_DEFAULTS
    if int(getattr(plan, "priority", 0) or 0) in _LEGACY_PRIORITY_DEFAULTS:
        changes["priority"] = PRIORITY_DEFAULT
    return replace(plan, **changes) if changes else plan


def _validate(plan: AccessPlan) -> None:
    if plan.plan_type not in PLAN_TYPES:
        raise RadiusValidationError(
            f"نوع الباقة غير معروف (المسموح: {'، '.join(PLAN_TYPES)}).")
    if len((plan.name or "").strip()) > PLAN_NAME_MAX:
        raise RadiusValidationError(
            f"اسم الباقة أطول من المسموح ({PLAN_NAME_MAX} حرفًا كحدّ أقصى).")
    for field, label in _NON_NEGATIVE_PLAN_FIELDS.items():
        try:
            value = float(getattr(plan, field, 0) or 0)
        except (TypeError, ValueError):
            continue
        if value < 0:
            raise RadiusValidationError(f"{label} لا يمكن أن يكون سالبًا.")
        if value > PLAN_INT_MAX:
            raise RadiusValidationError(f"قيمة «{label}» أكبر من المسموح.")
    for field, label in _INT_PLAN_FIELDS.items():
        try:
            value = int(getattr(plan, field, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
        if value < 0:
            raise RadiusValidationError(f"{label} لا يمكن أن يكون سالبًا.")
        cap = _INT_FIELD_MAX.get(field, PLAN_INT_MAX)
        if value > cap:
            raise RadiusValidationError(
                f"قيمة «{label}» أكبر من المسموح (الحدّ {cap:,}).".replace(",", "٬"))
    from ..db.repos.plans_repo import PRIORITY_MAX, PRIORITY_MIN
    if not PRIORITY_MIN <= int(getattr(plan, "priority", 0) or 0) <= PRIORITY_MAX:
        # مقياسٌ واحد للويب والـAPI والتطبيق (F04 N-L10).
        raise RadiusValidationError(
            f"«الأولويّة» رقمٌ من {PRIORITY_MIN} إلى {PRIORITY_MAX} "
            f"({PRIORITY_MIN} = الأعلى في القوائم والمتجر).")
    color = str(getattr(plan, "color", "") or "").strip()
    if color and not _COLOR_RE.match(color):
        # كان «<script>…» يُخزَّن ويُحقن في style="background:…".
        raise RadiusValidationError("لون الباقة يجب أن يكون رمزًا مثل ‎#2BAACC‎.")
    for field, label in _HOUR_FIELDS.items():
        raw = str(getattr(plan, field, "") or "").strip()
        if raw and not _HOUR_RE.match(raw):
            raise RadiusValidationError(
                f"«{label}» يجب أن تكون ساعةً صحيحة بصيغة HH:MM (00:00–23:59)، "
                f"والقيمة «{raw}» غير صالحة.")
    if plan.speed_down_kbps < 0 or plan.speed_up_kbps < 0:
        raise RadiusValidationError("السرعة لا يمكن أن تكون سالبة.")
    # 🔴 الصفرُ ليس «بلا حدّ» تلقائيًّا. ردٌّ بلا Mikrotik-Rate-Limit يجعل
    # الراوترَ يطبّق ملفَّه الافتراضيَّ (مفتوحًا عادةً)، فكان «نسيتُ
    # السرعة» و«أريدها مفتوحة» شيئًا واحدًا. المفتوحُ يُعلَّم صراحةً.
    if (plan.speed_down_kbps == 0 or plan.speed_up_kbps == 0)             and not plan.speed_unlimited:
        raise RadiusValidationError(
            "السرعة مطلوبة (تنزيل ورفع) — أو علّم «بلا حدّ للسرعة» صراحةً "
            "إن كانت الباقة مفتوحة.")
    if plan.concurrent_sessions < 1:
        raise RadiusValidationError("عدد الجلسات المتزامنة يجب أن يكون 1 على الأقل.")
    validate_service_scope(plan.service_scope)
    if plan.max_loan_minutes < 0:
        raise RadiusValidationError("الحدّ الأقصى لدقائق السلفة لا يمكن أن يكون سالبًا.")


def get_plans_service() -> PlansService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return PlansService(get_radius_adapter(), audit=get_audit_service())
