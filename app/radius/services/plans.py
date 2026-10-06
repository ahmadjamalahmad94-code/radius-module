"""PlansService — إدارة الباقات/العروض."""
from __future__ import annotations
from app.i18n_text import N_, _tr

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
CLONE_NAME_SUFFIX = N_(" - نسخة")


def _restamp_after_plan_duration_change(existing, saved, *, actor: str) -> None:
    """``duration_minutes``/``validity_days`` تغيّرت ⇒ أعِد ختمَ البطاقات التي
    بدأت على حِزم هذه الباقة (الحزمةُ ذاتُ المدّة الخاصّة لا تتأثّر — دالّةُ
    الميزانية تقرّر). لا يرمي أبدًا ولا يُبطئ الحفظ (الدفعُ للراوتر خلفيّ)."""
    if existing is None or saved is None or getattr(saved, "id", None) is None:
        return
    old = {"duration_minutes": int(getattr(existing, "duration_minutes", 0) or 0),
           "validity_days": int(getattr(existing, "validity_days", 0) or 0)}
    new = {"duration_minutes": int(getattr(saved, "duration_minutes", 0) or 0),
           "validity_days": int(getattr(saved, "validity_days", 0) or 0)}
    if old == new:
        return
    try:
        from .card_restamp import restamp_started_cards
        restamp_started_cards(
            int(getattr(saved, "tenant_id", 0) or 1), plan_id=int(saved.id),
            previous_plan=old, reason="plan_update", actor=actor)
    except Exception:  # noqa: BLE001 — حفظُ الباقة لا يسقط لأجل هذا
        pass


class PlansService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, limit: int = 200, offset: int = 0) -> Sequence[AccessPlan]:
        return self._adapter.list_profiles(limit=limit, offset=offset)

    def get(self, plan_id: int) -> AccessPlan:
        return self._adapter.get_profile(plan_id)

    def create(self, *, actor: str, plan: AccessPlan) -> AccessPlan:
        plan = _normalize(plan, existing=None)
        _validate(plan)
        _validate_speed_extras(plan, existing=None)
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
        base = (source_name or N_("عرض")).strip()
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
            raise RadiusValidationError(_tr("تعديل الباقة يتطلّب معرّفها."))
        try:                                  # لقطة «قبل» لعرض الفرق في السجلّ
            existing = self._adapter.get_profile(plan.id)
        except Exception:  # noqa: BLE001
            existing = None
        plan = _normalize(plan, existing=existing)
        _validate(plan)
        _validate_speed_extras(plan, existing=existing)
        plan = _claim_plan_name(plan)
        saved = self._adapter.upsert_profile(plan)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="plan", target_id=str(saved.id),
                           payload={"name": saved.name},
                           before=_plan_snapshot(existing),
                           after=_plan_snapshot(saved))
        # قرارُ المالك («أ»، 2026-10-05): تغييرُ مدّة الباقة يسري على البطاقات
        # التي **بدأت** في كلّ حزمةٍ تأخذ ميزانيّتها منها — من أوّل دخولها هي — قبل
        # إعادة فحص الجلسات أدناه كي تقرأ النهايةَ الجديدة.
        _restamp_after_plan_duration_change(existing, saved, actor=actor)
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
        # وتغيّرُ السرعة يصل الجلسات الحيّة فورًا (CoA) لا عند إعادة الاتّصال.
        try:
            from . import bandwidth_apply
            if bandwidth_apply.plan_speed_changed(existing, saved):
                bandwidth_apply.push_plan_speed_live(
                    int(getattr(saved, "tenant_id", 0) or 1), int(saved.id))
        except Exception:  # noqa: BLE001
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
                _tr("لا يمكن حذف الباقة: عليها %(s)d مشترك و%(b)d حزمة و%(c)d بطاقة — "
                "انقلهم إلى باقةٍ أخرى أوّلًا.") % {
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
        raise RadiusValidationError(_tr("اسم الباقة مطلوب."))
    if len(name) > PLAN_NAME_MAX:
        raise RadiusValidationError(
            _tr('اسم الباقة أطول من المسموح (%(PLAN_NAME_MAX)s حرفًا كحدّ أقصى).', PLAN_NAME_MAX=PLAN_NAME_MAX))
    if name != plan.name:
        plan = replace(plan, name=name)
    tid = int(getattr(plan, "tenant_id", 1) or 1)
    rows = db().execute(
        "SELECT id, name, deleted_at FROM access_plans "
        "WHERE tenant_id = ? AND lower(trim(name)) = lower(?)", (tid, name),
    ).fetchall()
    others = [r for r in rows if plan.id is None or int(r["id"]) != int(plan.id)]
    if any(r["deleted_at"] is None for r in others):
        raise RadiusValidationError(_tr('اسم الباقة «%(name)s» مستخدم مسبقًا لباقة أخرى.', name=name))
    for r in others:
        if r["name"] == name:  # الفهرس حسّاس للحالة: المطابق حرفيًّا فقط يحجز
            suffix = _tr(' (مؤرشفة #%(v)s)', v=int(r['id']))
            archived_name = name[:max(1, PLAN_NAME_MAX - len(suffix))].rstrip() + suffix
            with transaction() as conn:
                conn.execute(
                    "UPDATE access_plans SET name = ? WHERE tenant_id = ? AND id = ?",
                    (archived_name, tid, int(r["id"])))
    return plan


_NON_NEGATIVE_PLAN_FIELDS = {
    "price": N_("السعر"), "price_card": N_("سعر البطاقة"), "price_bulk": N_("سعر الجملة"),
    "duration_value": N_("المدّة"), "duration_minutes": N_("المدّة بالدقائق"),
    "validity_value": N_("الصلاحية"), "validity_days": N_("أيّام الصلاحية"),
    "quota_total_mb": N_("الكوتة الإجماليّة"), "quota_daily_mb": N_("الكوتة اليوميّة"),
    "quota_monthly_mb": N_("الكوتة الشهريّة"),
    "daily_download_quota_mb": N_("كوتة التنزيل اليوميّة"),
    "daily_upload_quota_mb": N_("كوتة الرفع اليوميّة"),
    "daily_combined_quota_mb": N_("الكوتة اليوميّة الإجماليّة"),
    "monthly_download_quota_mb": N_("كوتة التنزيل الشهريّة"),
    "monthly_upload_quota_mb": N_("كوتة الرفع الشهريّة"),
    "monthly_combined_quota_mb": N_("الكوتة الشهريّة الإجماليّة"),
}


# كلّ حقول الباقة العدديّة الصحيحة: غير سالبة وتحت سقفٍ عاقل (كانت 10^20
# و2^63 تُسقط SQLite بـ 500، وكانت الأجهزة/المهلات/الأولويّة/VLAN السالبة
# تُقبل). السقف الخاصّ لبعض الحقول في ``_INT_FIELD_MAX``.
PLAN_INT_MAX = 1_000_000_000
_INT_PLAN_FIELDS = {
    "max_daily_minutes": N_("الحدّ اليوميّ للدقائق"),
    "max_weekly_minutes": N_("الحدّ الأسبوعيّ للدقائق"),
    "max_monthly_minutes": N_("الحدّ الشهريّ للدقائق"),
    "session_timeout_sec": N_("مهلة الجلسة"),
    "idle_timeout_sec": N_("مهلة الخمول"),
    "data_value": N_("حجم البيانات"),
    "speed_up_kbps": N_("سرعة الرفع"),
    "speed_down_kbps": N_("سرعة التنزيل"),
    "burst_up_kbps": N_("سرعة الدفعة (رفع)"),
    "burst_down_kbps": N_("سرعة الدفعة (تنزيل)"),
    "burst_threshold_kbps": N_("عتبة الدفعة"),
    "burst_time_sec": N_("زمن الدفعة"),
    "cir_down_kbps": N_("السرعة المضمونة (تنزيل)"),
    "cir_up_kbps": N_("السرعة المضمونة (رفع)"),
    "concurrent_sessions": N_("عدد الجلسات المتزامنة"),
    "vlan_id": N_("رقم VLAN"),
    "allowed_devices_count": N_("عدد الأجهزة المسموحة"),
    "priority": N_("الأولويّة"),
    "max_consumption_times": N_("عدد مرّات الاستهلاك"),
    "ticket_validity_days": N_("صلاحية التذكرة بالأيام"),
    "working_hours_limit": N_("حدّ ساعات العمل"),
    "max_loan_minutes": N_("الحدّ الأقصى لدقائق السلفة"),
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
    "allowed_hours_from": N_("ساعة البداية"),
    "allowed_hours_to": N_("ساعة النهاية"),
    "offer_hours_from": N_("ساعات العرض — من"),
    "offer_hours_to": N_("ساعات العرض — إلى"),
    "nightly_from": N_("غير محدود ليلًا — من"),
    "nightly_to": N_("غير محدود ليلًا — إلى"),
}
_HOUR_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$|^24:00(?::00)?$")


def plan_field_label(field: str) -> str:
    """التسمية العربيّة لحقل باقة (لرسائل الخطأ) — الحقل الخام إن لم يُعرف."""
    return (_NON_NEGATIVE_PLAN_FIELDS.get(field) or _INT_PLAN_FIELDS.get(field)
            or _HOUR_FIELDS.get(field) or _OTHER_LABELS.get(field) or field)


_OTHER_LABELS = {
    "name": N_("اسم الباقة"), "enabled": N_("تفعيل الباقة"), "bind_mac": N_("ربط MAC"),
    "bind_ip": N_("ربط IP"), "force_mac_address": N_("فرض عنوان MAC"),
    "auto_renew": N_("التجديد التلقائيّ"), "prepaid": N_("الدفع المسبق"),
    "speed_control_enabled": N_("التحكّم بالسرعة"), "burst_enabled": N_("الدفعة (Burst)"),
    "nightly_unlimited_enabled": N_("الليل المفتوح"), "single_use_once": N_("استخدام مرّة واحدة"),
    "hotspot_enabled": N_("هوت سبوت"), "ppp_enabled": "PPP", "loan_enabled": N_("السلفة"),
    "speed_override_allowed": N_("تجاوز السرعة"), "speed_unlimited": N_("بلا حدّ للسرعة"),
    "shared_single_session": N_("جلسة واحدة فعّالة"), "bandwidth_id": N_("ملفّ السرعة"),
    "pool_id": N_("مجمّع العناوين"),
}


_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")


# نوع الخدمة: الهجاء القانونيّ للويب (بطاقتا «هوت سبوت»/«برودباند»).
_SERVICE_TYPE_CANON = {
    "hotspot": "Hotspot", "pppoe": "PPPoE", "broadband": "PPPoE", "both": "Both",
}


def scope_from_service_type(service_type: str) -> str:
    """نطاق الخدمة مشتقّ من «نوع الخدمة»: Hotspot→hotspot، PPPoE→broadband،
    Both→both (مصدرٌ واحد للويب والـAPI)."""
    t = (service_type or "").strip().lower()
    if t == "both":
        return "both"
    if t in ("pppoe", "broadband"):
        return "broadband"
    return "hotspot"


def derive_duration(minutes: int) -> tuple[int, str]:
    """(duration_value, duration_unit) من الدقائق — أيّام/ساعات/دقائق (MT71).
    التنفيذ على duration_minutes وحده؛ هذان للعرض و``_base_plan_minutes``."""
    m = int(minutes or 0)
    if m and m % 1440 == 0:
        return m // 1440, "Days"
    if m and m % 60 == 0:
        return m // 60, "Hrs"
    return m, "Mins"


def _service_changes(plan: AccessPlan, existing: AccessPlan | None) -> dict:
    """service_type قانونيّ + اشتقاق service_scope/hotspot_enabled/ppp_enabled
    منه (كان الـAPI يخزّن ما يصله: PPPoE مع scope=both وhotspot مفعّل).
    القيم القديمة (Balance/Voucher/Others) تبقى فقط إن لم تتغيّر."""
    raw = (plan.service_type or "").strip()
    canon = _SERVICE_TYPE_CANON.get(raw.lower())
    if canon is None:
        old = (getattr(existing, "service_type", "") or "").strip() if existing else ""
        if raw and raw == old:
            return {}                       # قيمةٌ قديمة لم تتغيّر — لا نمسّها
        if raw:
            raise RadiusValidationError(
                _tr('نوع الخدمة «%(raw)s» غير معروف (المسموح: Hotspot / PPPoE / Both).', raw=raw))
        canon = "Hotspot"
    return {
        "service_type": canon,
        "service_scope": scope_from_service_type(canon),
        "hotspot_enabled": canon in ("Hotspot", "Both"),
        "ppp_enabled": canon in ("PPPoE", "Both"),
    }


def _duration_changes(plan: AccessPlan, existing: AccessPlan | None) -> dict:
    """duration_value/unit تُشتقّ من duration_minutes عند كل حفظ (ويب وAPI) —
    كان PATCH الدقائق يترك «8 Hrs» مع 1440 دقيقة. صفر دقائق: يُصفَّر الزوج فقط
    إن كانت الدقائق قبلها موجبة (باقةٌ قديمة بزوجٍ بلا دقائق تبقى كما هي)."""
    minutes = int(plan.duration_minutes or 0)
    if minutes <= 0 and not (existing is not None
                             and int(existing.duration_minutes or 0) > 0):
        return {}
    val, unit = derive_duration(minutes)
    if (plan.duration_value, plan.duration_unit) == (val, unit):
        return {}
    return {"duration_value": val, "duration_unit": unit}


def _normalize(plan: AccessPlan, existing: AccessPlan | None = None) -> AccessPlan:
    """تطبيعٌ مشترك للويب والـAPI: نوع الخدمة ومشتقّاته، زوج المدّة من
    الدقائق، نطاق الخدمة بأحرفٍ صغيرة، والأرقام العشريّة ‎-0.0 ⇒ 0."""
    changes = {}
    changes.update(_service_changes(plan, existing))
    changes.update(_duration_changes(plan, existing))
    scope = (changes.get("service_scope", plan.service_scope) or "").strip().lower()
    if scope != changes.get("service_scope", plan.service_scope):
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
            _tr('نوع الباقة غير معروف (المسموح: %(v)s).', v='، '.join(PLAN_TYPES)))
    if len((plan.name or "").strip()) > PLAN_NAME_MAX:
        raise RadiusValidationError(
            _tr('اسم الباقة أطول من المسموح (%(PLAN_NAME_MAX)s حرفًا كحدّ أقصى).', PLAN_NAME_MAX=PLAN_NAME_MAX))
    for field, label in _NON_NEGATIVE_PLAN_FIELDS.items():
        try:
            value = float(getattr(plan, field, 0) or 0)
        except (TypeError, ValueError):
            continue
        if value < 0:
            raise RadiusValidationError(_tr('%(label)s لا يمكن أن يكون سالبًا.', label=label))
        if value > PLAN_INT_MAX:
            raise RadiusValidationError(_tr('قيمة «%(label)s» أكبر من المسموح.', label=label))
    for field, label in _INT_PLAN_FIELDS.items():
        try:
            value = int(getattr(plan, field, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            raise RadiusValidationError(_tr('قيمة «%(label)s» يجب أن تكون رقمًا صحيحًا.', label=label))
        if value < 0:
            raise RadiusValidationError(_tr('%(label)s لا يمكن أن يكون سالبًا.', label=label))
        cap = _INT_FIELD_MAX.get(field, PLAN_INT_MAX)
        if value > cap:
            raise RadiusValidationError(
                _tr('قيمة «%(label)s» أكبر من المسموح (الحدّ %(cap)s).', label=label, cap=format(cap, ',')).replace(",", "٬"))
    from ..db.repos.plans_repo import PRIORITY_MAX, PRIORITY_MIN
    if not PRIORITY_MIN <= int(getattr(plan, "priority", 0) or 0) <= PRIORITY_MAX:
        # مقياسٌ واحد للويب والـAPI والتطبيق (F04 N-L10).
        raise RadiusValidationError(
            _tr('«الأولويّة» رقمٌ من %(PRIORITY_MIN)s إلى %(PRIORITY_MAX)s (%(PRIORITY_MIN)s = الأعلى في القوائم والمتجر).', PRIORITY_MIN=PRIORITY_MIN, PRIORITY_MAX=PRIORITY_MAX))
    color = str(getattr(plan, "color", "") or "").strip()
    if color and not _COLOR_RE.match(color):
        # كان «<script>…» يُخزَّن ويُحقن في style="background:…".
        raise RadiusValidationError(_tr("لون الباقة يجب أن يكون رمزًا مثل ‎#2BAACC‎."))
    for field, label in _HOUR_FIELDS.items():
        raw = str(getattr(plan, field, "") or "").strip()
        if raw and not _HOUR_RE.match(raw):
            raise RadiusValidationError(
                _tr('«%(label)s» يجب أن تكون ساعةً صحيحة بصيغة HH:MM (00:00–23:59)، والقيمة «%(raw)s» غير صالحة.', label=label, raw=raw))
    if plan.speed_down_kbps < 0 or plan.speed_up_kbps < 0:
        raise RadiusValidationError(_tr("السرعة لا يمكن أن تكون سالبة."))
    # 🔴 الصفرُ ليس «بلا حدّ» تلقائيًّا. ردٌّ بلا Mikrotik-Rate-Limit يجعل
    # الراوترَ يطبّق ملفَّه الافتراضيَّ (مفتوحًا عادةً)، فكان «نسيتُ
    # السرعة» و«أريدها مفتوحة» شيئًا واحدًا. المفتوحُ يُعلَّم صراحةً.
    if (plan.speed_down_kbps == 0 or plan.speed_up_kbps == 0)             and not plan.speed_unlimited:
        raise RadiusValidationError(
            _tr("السرعة مطلوبة (تنزيل ورفع) — أو علّم «بلا حدّ للسرعة» صراحةً "
            "إن كانت الباقة مفتوحة."))
    if plan.concurrent_sessions < 1:
        raise RadiusValidationError(_tr("عدد الجلسات المتزامنة يجب أن يكون 1 على الأقل."))
    validate_service_scope(plan.service_scope)
    if plan.max_loan_minutes < 0:
        raise RadiusValidationError(_tr("الحدّ الأقصى لدقائق السلفة لا يمكن أن يكون سالبًا."))


# حقول «الدفعة/المضمونة/الليل» — تُفحَص فقط حين تتغيّر (أو باقةٌ جديدة) كي لا
# يسقط حفظُ باقةٍ قديمة محفوظةٍ بقيمٍ غير متّسقة لم يلمسها أحد (التطبيق يرسل
# كلّ الحقول في كلّ حفظ).
_BURST_FIELDS = ("burst_enabled", "burst_down_kbps", "burst_up_kbps",
                 "burst_threshold_kbps", "burst_time_sec",
                 "speed_down_kbps", "speed_up_kbps", "speed_unlimited")
_CIR_FIELDS = ("cir_down_kbps", "cir_up_kbps", "speed_down_kbps",
               "speed_up_kbps", "speed_unlimited")
_NIGHT_FIELDS = ("nightly_unlimited_enabled", "nightly_from", "nightly_to")


def _changed(plan, existing, fields) -> bool:
    if existing is None:
        return True
    return any(getattr(plan, f, None) != getattr(existing, f, None) for f in fields)


def _validate_speed_extras(plan: AccessPlan, existing: AccessPlan | None) -> None:
    """Burst وCIR و«غير محدود ليلًا» صارت تُطبَّق فعلًا (سطر Mikrotik-Rate-Limit
    وكوتة الليل) — فالقيمة غير المتّسقة تُرفض بدل أن تُحفظ بلا أثر.

    • Burst (مفعَّل): سرعة الدفعة أعلى من سرعة الباقة في الاتجاهين، والعتبة
      موجبة وأقلّ من سرعة دفعة التنزيل، والمدّة موجبة. ولا Burst لباقةٍ بلا حدّ.
    • CIR: لا يتجاوز سرعة الباقة في اتجاهه، ولا معنى له لباقةٍ بلا حدّ.
    • الليل (مفعَّل): «من» و«إلى» مطلوبتان ومختلفتان."""
    if bool(plan.burst_enabled) and _changed(plan, existing, _BURST_FIELDS):
        if plan.speed_unlimited or not (plan.speed_down_kbps and plan.speed_up_kbps):
            raise RadiusValidationError(
                _tr("السرعة المؤقتة (Burst) تحتاج سرعةً محدّدة للباقة (تنزيل ورفع)."))
        if (int(plan.burst_down_kbps or 0) <= int(plan.speed_down_kbps or 0)
                or int(plan.burst_up_kbps or 0) <= int(plan.speed_up_kbps or 0)):
            raise RadiusValidationError(
                _tr("سرعة Burst (تنزيل ورفع) يجب أن تكون أعلى من سرعة الباقة."))
        thr = int(plan.burst_threshold_kbps or 0)
        if thr <= 0 or thr >= int(plan.burst_down_kbps or 0):
            raise RadiusValidationError(
                _tr("«حد Burst» مطلوب، وأقلّ من سرعة Burst للتنزيل."))
        if int(plan.burst_time_sec or 0) <= 0:
            raise RadiusValidationError(_tr("«مدة Burst» مطلوبة بالثواني."))
    cir_d, cir_u = int(plan.cir_down_kbps or 0), int(plan.cir_up_kbps or 0)
    if (cir_d or cir_u) and _changed(plan, existing, _CIR_FIELDS):
        if plan.speed_unlimited:
            raise RadiusValidationError(
                _tr("السرعة المضمونة (CIR) تحتاج سرعةً محدّدة للباقة."))
        if cir_d > int(plan.speed_down_kbps or 0) or cir_u > int(plan.speed_up_kbps or 0):
            raise RadiusValidationError(
                _tr("السرعة المضمونة (CIR) لا تتجاوز سرعة الباقة في اتجاهها."))
    if bool(plan.nightly_unlimited_enabled) and _changed(plan, existing, _NIGHT_FIELDS):
        f = str(plan.nightly_from or "").strip()
        t = str(plan.nightly_to or "").strip()
        if not f or not t:
            raise RadiusValidationError(
                _tr("«غير محدود ليلًا» يحتاج بداية ونهاية الفترة الليلية (من / إلى)."))
        if f[:5] == t[:5]:
            raise RadiusValidationError(
                _tr("بداية الفترة الليلية ونهايتها لا تتساويان."))


def get_plans_service() -> PlansService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return PlansService(get_radius_adapter(), audit=get_audit_service())
