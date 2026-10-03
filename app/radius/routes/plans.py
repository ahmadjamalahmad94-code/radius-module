"""Plans (العروض) routes — CRUD.

RM-H3: extended with full AdvRadius scope.
Hybrid storage:
  - الحقول queryable كأعمدة DB حقيقية (migration 012).
  - الحقول vendor-specific / المتقدمة في metadata JSON مُجمَّع
    {general, subscription, advanced, mikrotik, notifications}.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import PLAN_TYPES
from ..core.errors import RadiusError, RadiusValidationError
from ..core.system_config import default_currency
from ..core.types import AccessPlan
from ..services.plans import get_plans_service
from .speed_rules_ui import handle_embedded_speed_rule, speed_rules_panel
from ..core.numbers import strict_float  # Infinity/NaN → ValueError (422/flash)


# ════════════════════════════════════════════════════════════════
# RM-H3: metadata structure مُجمَّعة (5 أقسام)
# ════════════════════════════════════════════════════════════════
_META_GROUPS = {
    "general": [
        # حقول DNS/Cisco/Connection file التي ليست محورية في query
        "download_policy_cisco", "upload_policy_cisco",
        "device_connection_file", "primary_dns_ppp", "secondary_dns_ppp",
    ],
    "subscription": [
        "shared_voucher_fup",
        "equal_download_speed", "equal_upload_speed",
        "send_alerts", "renewal_method", "billing_method",
        "user_can_change_offer", "user_can_request_offer_change",
        "force_subscriber_to_purchase_card", "hide_invoice",
        "subscriber_control_panel_enabled", "subscription_expiry_date",
        "prevent_user_from_changing_subscription",
        "prevent_admin_from_changing_user_subscription",
        "equal_quota_sharing",
        "daily_connection_time", "internet_connection_time",
        "save_remaining_quota_on_activation",
        "use_old_quota_and_sessions_on_activation",
        "delete_usage_data_and_sessions_on_activation",
        "carry_remaining_time_on_activation",
        "notify_when_quota_reaches_zero",
        "stop_user_when_time_expires",
    ],
    "advanced": [
        "auto_renew_when_quota_expires",
        "auto_renew_when_time_expires",
        "time_expiry_policy",
        "only_available_for", "available_for_all", "all_days",
    ],
    "mikrotik": [
        "enable_mtu", "expiry_day_limit_toggle", "expiry_hour_limit_toggle",
        "mikrotik_address_list", "mikrotik_filter_chain_name",
        "mikrotik_user_group", "mikrotik_queue_priority_simple_queue",
    ],
    "notifications": [
        "notify_before_quota_expiry",
        "notify_before_daily_quota_expiry",
        "notification_channels",
    ],
}
_META_FIELDS = [f for g in _META_GROUPS.values() for f in g]


def _grouped_to_flat(grouped: dict) -> dict:
    out = {}
    for grp in (grouped or {}).values():
        if isinstance(grp, dict):
            out.update(grp)
    return out


def _flat_to_grouped(flat: dict) -> dict:
    grouped = {g: {} for g in _META_GROUPS}
    for grp, fields in _META_GROUPS.items():
        for f in fields:
            v = flat.get(f, "")
            if v not in (None, ""):
                grouped[grp][f] = v
    return grouped


def _parse_metadata(raw) -> dict:
    if isinstance(raw, dict): data = raw
    else:
        try: data = json.loads(raw or "{}") or {}
        except (ValueError, TypeError): data = {}
    for g in _META_GROUPS: data.setdefault(g, {})
    return data


def _plan_with_meta_for_template(plan: AccessPlan) -> dict:
    """يحوّل plan إلى dict + يسطّح metadata للوصول البسيط من القالب."""
    from dataclasses import asdict
    d = asdict(plan)
    grouped = _parse_metadata(plan.metadata)
    flat = _grouped_to_flat(grouped)
    for f in _META_FIELDS:
        d.setdefault(f, flat.get(f, ""))
    return d


def register_plans_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/plans", "plans_list", plans_list, methods=["GET"])
    bp.add_url_rule("/plans/new", "plans_new", plans_new, methods=["GET"])
    bp.add_url_rule("/plans", "plans_create", plans_create, methods=["POST"])
    bp.add_url_rule("/plans/<int:plan_id>/edit", "plans_edit", plans_edit, methods=["GET"])
    bp.add_url_rule("/plans/<int:plan_id>", "plans_update", plans_update, methods=["POST"])
    bp.add_url_rule("/plans/<int:plan_id>/clone", "plans_clone", plans_clone, methods=["POST"])
    bp.add_url_rule("/plans/<int:plan_id>/delete", "plans_delete", plans_delete, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")


def _num_text(name: str) -> str:
    """النصّ الخام للحقل بأرقامٍ لاتينيّة («١٢٫٥» → «12.5»)."""
    return (request.form.get(name) or "").strip().translate(_AR_DIGITS).replace("٬", "")


def _i(name: str, default: int = 0) -> int:
    raw = _num_text(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:  # «1440.0» من حقلٍ عشريّ — عددٌ صحيح فعلًا
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        if value is not None and value.is_integer() and abs(value) < 1e15:
            return int(value)
        from ..services.plans import plan_field_label
        raise RadiusValidationError(
            _tr('قيمة «%(v)s» يجب أن تكون رقمًا صحيحًا.', v=plan_field_label(name)))


def _f(name: str, default: float = 0.0) -> float:
    # 🔴 كانت القيمة غير المقروءة («abc»، inf، nan) تصير 0 بصمت ⇒ باقةٌ مجّانيّة
    # مع «تم إنشاء الباقة». الآن تُرفض برسالةٍ عربيّة ويُعاد النموذج.
    raw = _num_text(name)
    if not raw:
        return default
    try:
        return strict_float(raw, name) + 0.0
    except (TypeError, ValueError):
        from ..services.plans import plan_field_label
        raise RadiusValidationError(
            _tr('قيمة «%(v)s» يجب أن تكون رقمًا صحيحًا منتهيًا.', v=plan_field_label(name)))


def _b(name: str) -> bool:
    return request.form.get(name, "") in ("1", "on", "true", "yes")


def _s(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _service_type_from_form() -> str:
    """نوع الخدمة — بطاقات متعددة الاختيار (هوت سبوت + برودباند) مثل
    نموذج المشترك. تُجمع الاختيارات إلى قيمة واحدة في نفس العمود:
    Hotspot / PPPoE / Both. مع التوافق الخلفي لو وصلت قيمة مفردة قديمة."""
    raw = [v.strip().lower() for v in request.form.getlist("service_type") if v.strip()]
    has_hs = any(v in ("hotspot",) for v in raw)
    has_ppp = any(v in ("pppoe", "broadband") for v in raw)
    if any(v == "both" for v in raw) or (has_hs and has_ppp):
        return "Both"
    if has_ppp:
        return "PPPoE"
    return "Hotspot"


def _scope_from_service_type(service_type: str) -> str:
    """نطاق الخدمة (hotspot/broadband/both) مشتقّ من «نوع الخدمة» بدل حقل
    منفصل مكرّر: حُذف حقل «نطاق الخدمة» من النموذج لأنه يكرّر بطاقات نوع
    الخدمة، ونشتقّه هنا حتى يحفظ العرض بقابلية الخدمة الصحيحة.
        Hotspot → hotspot، PPPoE → broadband، Both → both.
    المنطق في ``services.plans.scope_from_service_type`` (مشترك مع الـAPI)."""
    from ..services.plans import scope_from_service_type
    return scope_from_service_type(service_type)


def _form_to_dto(*, plan_id: int | None = None) -> AccessPlan:
    days_raw = request.form.getlist("allowed_days") or ["mon","tue","wed","thu","fri","sat","sun"]

    # metadata: collect flat from form, group into JSON
    flat_meta = {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if v:
            flat_meta[mf] = v
    meta_json = json.dumps(_flat_to_grouped(flat_meta), ensure_ascii=False)

    service_type = _service_type_from_form()
    # «نوع الخدمة» هو مصدر الحقيقة الوحيد لتفعيل الخدمة (أُزيلت مفاتيح
    # «بوابة الدخول»/«PPP» المكرّرة من قسم «خدمات الاتصال»): يُشتقّ منه
    # hotspot_enabled/ppp_enabled مباشرةً فلا يُفقَد أي تفعيل.
    hotspot_enabled = service_type in ("Hotspot", "Both")
    ppp_enabled = service_type in ("PPPoE", "Both")

    # MT71 — الوحدة تُشتقّ من الدقائق عند كل حفظ (منتقي المدة يُرسل الدقائق
    # فقط). الاشتقاق صار في ``services.plans._normalize`` المشترك مع الـAPI
    # (كان PATCH الدقائق يترك «8 Hrs» مع 1440 دقيقة)؛ نحسبه هنا أيضًا ليُعاد
    # عرض النموذج صادقًا عند خطأ تحقّق.
    from ..services.plans import derive_duration
    _dur_min = _i("duration_minutes")
    _dur_val, _dur_unit = derive_duration(_dur_min)

    return AccessPlan(
        id=plan_id,
        name=_s("name"),
        code=_s("code"),
        plan_type=_s("plan_type").lower() or "time",
        service_type=service_type,
        duration_minutes=_dur_min,
        duration_value=_dur_val,
        duration_unit=_dur_unit,
        validity_days=_i("validity_days"),
        max_daily_minutes=_i("max_daily_minutes"),
        max_weekly_minutes=_i("max_weekly_minutes"),
        max_monthly_minutes=_i("max_monthly_minutes"),
        quota_total_mb=_i("quota_total_mb"),
        quota_daily_mb=_i("quota_daily_mb"),
        quota_monthly_mb=_i("quota_monthly_mb"),
        quota_reset_strategy=_s("quota_reset_strategy") or "rolling",
        speed_up_kbps=_i("speed_up_kbps"),
        speed_down_kbps=_i("speed_down_kbps"),
        burst_up_kbps=_i("burst_up_kbps"),
        burst_down_kbps=_i("burst_down_kbps"),
        burst_threshold_kbps=_i("burst_threshold_kbps"),
        burst_time_sec=_i("burst_time_sec"),
        concurrent_sessions=max(1, _i("concurrent_sessions", 1)),
        session_timeout_sec=_i("session_timeout_sec"),
        idle_timeout_sec=_i("idle_timeout_sec"),
        address_pool=_s("address_pool"),
        framed_pool=_s("framed_pool"),
        vlan_id=_i("vlan_id"),
        ipv6_pool=_s("ipv6_pool"),
        bind_mac=_b("bind_mac"),
        bind_ip=_b("bind_ip"),
        allowed_days=tuple(days_raw),
        allowed_hours_from=_s("allowed_hours_from"),
        allowed_hours_to=_s("allowed_hours_to"),
        price=_f("price"),
        currency=_s("currency") or default_currency(),
        description=_s("description"),
        enabled=_b("enabled"),
        # الأولوية: ترتيب ظهور العرض في القوائم/المتجر فقط (لا أثر وظيفيّ).
        # يُقيَّد 1–10 (افتراضيّ 5)، وتُطبَّع القيم القديمة (مثل 100) عند الحفظ.
        priority=min(10, max(1, _i("priority", 5))),
        color=_s("color") or "#F4BA2A",
        # RM-H3 fields
        speed_control_enabled=_b("speed_control_enabled"),
        cir_down_kbps=_i("cir_down_kbps"),
        cir_up_kbps=_i("cir_up_kbps"),
        burst_enabled=_b("burst_enabled"),
        nightly_unlimited_enabled=_b("nightly_unlimited_enabled"),
        speed_unlimited=_b("speed_unlimited"),
        monthly_download_quota_mb=_i("monthly_download_quota_mb"),
        monthly_upload_quota_mb=_i("monthly_upload_quota_mb"),
        monthly_combined_quota_mb=_i("monthly_combined_quota_mb"),
        daily_download_quota_mb=_i("daily_download_quota_mb"),
        daily_upload_quota_mb=_i("daily_upload_quota_mb"),
        daily_combined_quota_mb=_i("daily_combined_quota_mb"),
        single_use_once=_b("single_use_once"),
        max_consumption_times=_i("max_consumption_times"),
        ticket_validity_days=_i("ticket_validity_days"),
        working_hours_limit=_i("working_hours_limit"),
        # مشتقّان من «نوع الخدمة» (لا مفاتيح مكرّرة في «خدمات الاتصال»).
        hotspot_enabled=hotspot_enabled,
        ppp_enabled=ppp_enabled,
        # نطاق الخدمة مشتقّ من «نوع الخدمة» (حُذف الحقل المكرّر من النموذج)
        service_scope=_scope_from_service_type(service_type),
        loan_enabled=_b("loan_enabled"),
        max_loan_minutes=_i("max_loan_minutes"),
        speed_override_allowed=_b("speed_override_allowed"),
        shared_single_session=_b("shared_single_session"),
        offer_hours_from=_s("offer_hours_from"),
        offer_hours_to=_s("offer_hours_to"),
        connection_schedule=_normalize_connection_schedule(_s("connection_schedule")),
        metadata=meta_json,
    )


# ── حقول AccessPlan التي **يعرضها** نموذج الويب فعلًا (بما فيها المشتقّة من
# حقلٍ معروض: duration_* من duration_minutes، والنطاق/التفعيل من service_type
# — يشتقّها ``services.plans._normalize``). عند التعديل تُؤخذ هذه فقط من
# النموذج وكل ما سواها يبقى من الباقة المحفوظة: كان فتح الباقة وحفظها بلا
# تغيير يصفّر ~15 حقلًا لا يعرضها النموذج (الأيام المسموحة، عدد الأجهزة، فرض
# MAC، الفئة، الدفع المسبق، التجديد التلقائيّ، سعر البطاقة/الجملة، PPP،
# النطاق…). metadata يُدمج لا يُستبدل.
_WEB_FORM_FIELDS = (
    "name", "code", "plan_type", "service_type",
    "duration_minutes", "duration_value", "duration_unit",
    "validity_days",
    "max_daily_minutes", "max_weekly_minutes", "max_monthly_minutes",
    "quota_total_mb", "quota_daily_mb", "quota_monthly_mb", "quota_reset_strategy",
    "speed_up_kbps", "speed_down_kbps",
    "burst_up_kbps", "burst_down_kbps", "burst_threshold_kbps", "burst_time_sec",
    "concurrent_sessions", "session_timeout_sec", "idle_timeout_sec",
    "address_pool", "framed_pool", "vlan_id", "ipv6_pool",
    "bind_mac", "bind_ip",
    "price", "currency", "description", "enabled", "priority", "color",
    "speed_control_enabled", "cir_down_kbps", "cir_up_kbps",
    "burst_enabled", "nightly_unlimited_enabled", "speed_unlimited",
    "monthly_download_quota_mb", "monthly_upload_quota_mb", "monthly_combined_quota_mb",
    "daily_download_quota_mb", "daily_upload_quota_mb", "daily_combined_quota_mb",
    "single_use_once", "max_consumption_times", "ticket_validity_days",
    "working_hours_limit",
    "loan_enabled", "max_loan_minutes", "speed_override_allowed",
    "shared_single_session", "offer_hours_from", "offer_hours_to",
    "connection_schedule",
)


def _merge_metadata(existing_raw, form_flat: dict) -> str:
    """metadata المحفوظة + حقول النموذج (``_META_FIELDS``) فوقها. المفاتيح
    خارج ``_META_FIELDS`` (من التطبيق/الـAPI) تبقى؛ حقلٌ معروضٌ فارغ في النموذج
    (مفتاحٌ مُطفأ) يُزال من مجموعته — ونسخته المسطّحة القديمة في الجذر تُزال
    أيضًا كي لا تُقرأ بديلًا."""
    try:
        raw_obj = json.loads(existing_raw or "{}") if isinstance(existing_raw, str) else existing_raw
        present = set(raw_obj) if isinstance(raw_obj, dict) else set()
    except (ValueError, TypeError):
        present = set()
    try:
        data = _parse_metadata(existing_raw)
    except Exception:  # noqa: BLE001 — metadata ليست كائنًا (قائمة) ⇒ نبدأ نظيفًا
        data = _parse_metadata({})
    data = {k: (dict(v) if isinstance(v, dict) else v) for k, v in data.items()}
    for grp, fields in _META_GROUPS.items():
        bucket = data.get(grp)
        if not isinstance(bucket, dict):
            bucket = {}
            data[grp] = bucket
        for f in fields:
            v = form_flat.get(f, "")
            if v not in (None, ""):
                bucket[f] = v
            else:
                bucket.pop(f, None)
            if f in data and not isinstance(data.get(f), dict):
                data.pop(f, None)
        if not bucket and grp not in present:   # لا نُضيف مجموعاتٍ فارغة
            data.pop(grp, None)
    return json.dumps(data, ensure_ascii=False)


def _form_meta_flat() -> dict:
    out = {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if v:
            out[mf] = v
    return out


def _merge_web_form(existing: AccessPlan, dto: AccessPlan) -> AccessPlan:
    """الباقة المحفوظة + ما يعرضه النموذج فقط (انظر ``_WEB_FORM_FIELDS``)."""
    from dataclasses import replace
    changes = {f: getattr(dto, f) for f in _WEB_FORM_FIELDS}
    # نوع خدمة قديم (Balance/Voucher…) يعرضه النموذج «هوت سبوت»: يبقى كما هو
    # ما لم يغيّر المستخدم البطاقتين فعلًا.
    old_st = (existing.service_type or "").strip()
    if old_st and old_st.lower() not in ("hotspot", "pppoe", "broadband", "both") \
            and dto.service_type == "Hotspot":
        changes["service_type"] = existing.service_type
    changes["metadata"] = _merge_metadata(existing.metadata, _form_meta_flat())
    return replace(existing, **changes)


def _normalize_connection_schedule(raw: str) -> str:
    """Validated round-trip through access_schedule.serialize."""
    if not raw:
        return ""
    try:
        from ..core.access_schedule import serialize
        return serialize(raw)
    except Exception:  # noqa: BLE001
        return ""


# ─────────────── views ───────────────

_FRAGMENT_TPL = (
    '{% import "radius/_plans_fragments.html" as frag with context %}'
    '{% if mode == "detail" %}{{ frag.plan_detail(plan) }}'
    '{% else %}{{ frag.cards_grid(items, sub_counts) }}{% endif %}')


def plans_list():
    fragment = (request.args.get("fragment") or "").strip()
    if fragment == "detail":
        # محتوى نافذة «تفاصيل الباقة» لباقةٍ واحدة — يُجلب عند النقر.
        try:
            plan = get_plans_service().get(int(request.args.get("id") or 0))
        except (TypeError, ValueError, RadiusError):
            abort(404)
        from flask import render_template_string
        return render_template_string(_FRAGMENT_TPL, mode="detail", plan=plan)
    items = get_plans_service().list(limit=500)

    # عدّاد المشتركين لكل باقة (استعلام واحد رخيص) — لعمود «المشتركون»
    # و KPI «المشتركون الموزَّعون». يُحاط بـ try حتى لا يكسر المحوّلات
    # غير المعتمدة على SQLite (MikroTik/manual).
    sub_counts: dict[int, int] = {}
    try:
        from flask import g
        from ..db.connection import db
        tid = int(getattr(g, "tenant_id", None) or _tid())
        cur = db().execute(
            "SELECT plan_id, COUNT(*) AS c FROM subscribers "
            "WHERE tenant_id = ? AND plan_id IS NOT NULL GROUP BY plan_id",
            (tid,))
        sub_counts = {int(r["plan_id"]): int(r["c"]) for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        sub_counts = {}

    if fragment == "cards":
        from flask import render_template_string
        return render_template_string(_FRAGMENT_TPL, mode="cards", items=items,
                                      sub_counts=sub_counts)
    return render_template("radius/plans_list.html", items=items, sub_counts=sub_counts)


def plans_new():
    empty = AccessPlan(id=None, name="", enabled=True)
    return render_template("radius/plans_form.html",
        plan=_plan_with_meta_for_template(empty),
        plan_types=PLAN_TYPES, is_new=True, speed_rules_panel=None)


def plans_create():
    try:
        dto = _form_to_dto()
    except RadiusError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.plans_new"))
    try:
        saved = get_plans_service().create(actor=_actor(), plan=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/plans_form.html",
            plan=_plan_with_meta_for_template(dto), plan_types=PLAN_TYPES,
            is_new=True, speed_rules_panel=None), 400
    flash(_tr('تم إنشاء الباقة «%(name)s».', name=saved.name), "success")
    return redirect(url_for("radius.plans_list"))


def plans_edit(plan_id: int):
    try:
        plan = get_plans_service().get(plan_id)
    except RadiusError:
        abort(404)
    return render_template("radius/plans_form.html",
        plan=_plan_with_meta_for_template(plan),
        plan_types=PLAN_TYPES,
        is_new=False,
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="plan",
            plan_id=plan_id,
            return_to=request.path,
            title=N_("قواعد سرعة هذه الباقة"),
            help_text=N_("أضف قواعد سرعة متغيرة لهذه الباقة حسب الوقت. إذا وُجدت قاعدة للمشترك أو حزمة البطاقات فهي تتقدم على قاعدة الباقة."),
        ))


def plans_update(plan_id: int):
    if request.form.get("_speed_rule_action"):
        try:
            handle_embedded_speed_rule(
                tenant_id=_tid(),
                actor=_actor(),
                form=request.form,
                target_type="plan",
                plan_id=plan_id,
            )
            flash(_tr("تم تنفيذ إجراء قواعد السرعة لهذه الباقة."), "success")
        except RadiusError as e:
            flash(e.message, "error")
        return redirect(url_for("radius.plans_edit", plan_id=plan_id))

    try:
        existing = get_plans_service().get(plan_id)
    except RadiusError:
        abort(404)
    # علمَا «توزيع متساوٍ» (= تقسيم السرعة على الأجهزة) قبل الحفظ — لكشف التغيير.
    _old_split = _plan_split_flags(existing)

    try:
        dto = _merge_web_form(existing, _form_to_dto(plan_id=plan_id))
    except RadiusError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.plans_edit", plan_id=plan_id))
    try:
        saved = get_plans_service().update(actor=_actor(), plan=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/plans_form.html",
            plan=_plan_with_meta_for_template(dto), plan_types=PLAN_TYPES,
            is_new=False, speed_rules_panel=None), 400
    # توريث «توزيع متساوٍ» لمشتركي العرض: حين يتغيّر علم العرض فقط، اكتب القيمة
    # الجديدة لكلّ مشتركيه (فيبينوا فعّالين) وادفع السرعة المقسَّمة للجلسات الحيّة
    # بـCoA. لا نمسّهم إلّا عند التغيّر — فالاستثناء الفرديّ (تعطيله لمشترك بعدها)
    # يبقى بين تغييرات العرض.
    _new_split = (bool(request.form.get("equal_download_speed")),
                  bool(request.form.get("equal_upload_speed")))
    if _new_split != _old_split:
        try:
            _propagate_plan_split(plan_id, _new_split[0], _new_split[1])
        except Exception:  # noqa: BLE001 — التوريث لا يكسر حفظ العرض
            pass
    flash(_tr('تم تحديث «%(name)s».', name=saved.name), "success")
    return redirect(url_for("radius.plans_list"))


def plans_clone(plan_id: int):
    """Duplicate an existing offer, then land on the copy's edit page.

    RBAC is enforced upstream by the blueprint permission guard (same
    ``plans.create`` key as adding a plan); the source is fetched within the
    caller's tenant scope, so a limited manager can only clone offers they may
    access. CSRF is auto-injected server-side on the POST form.
    """
    try:
        saved = get_plans_service().clone(actor=_actor(), plan_id=plan_id)
    except RadiusError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.plans_list"))
    flash(_tr('تم إنشاء نسخة «%(name)s». يمكنك تعديلها الآن.', name=saved.name), "success")
    return redirect(url_for("radius.plans_edit", plan_id=saved.id))


def _plan_split_flags(plan) -> tuple[bool, bool]:
    """(توزيع_تنزيل, توزيع_رفع) من metadata العرض — الافتراضيّ (False, False).
    العلَمان مخزّنان تحت ``metadata.subscription`` (كانت القراءة من الجذر
    فتُرى دائمًا False: إطفاؤه لا يُورَّث، وتشغيله يُعاد توريثه مع كل حفظ).
    المفتاح المسطّح في الجذر (صيغة قديمة) بديلٌ فقط حين يغيب عن المجموعات."""
    def _on(v) -> bool:
        return str(v).strip().lower() in ("1", "true", "on", "t", "yes")
    try:
        raw = _parse_metadata(getattr(plan, "metadata", "") or "{}")
        flat = _grouped_to_flat(raw)

        def _get(k):
            if k in flat:
                return flat.get(k)
            v = raw.get(k)
            return None if isinstance(v, dict) else v
        return (_on(_get("equal_download_speed")), _on(_get("equal_upload_speed")))
    except Exception:  # noqa: BLE001
        return (False, False)


def _plan_split_flags_by_id(plan_id: int) -> tuple[bool, bool]:
    try:
        return _plan_split_flags(get_plans_service().get(plan_id))
    except Exception:  # noqa: BLE001
        return (False, False)


def _propagate_plan_split(plan_id: int, ed: bool, eu: bool) -> None:
    """توريث «تقسيم السرعة» — **للمشتركين فقط** (قرار المالك): البطاقات لا
    تَرِث من العرض؛ قالبها هو عرض الكروت وقت التوليد. المنطق في bandwidth_apply."""
    from ..services.bandwidth_apply import propagate_plan_split
    propagate_plan_split(_tid(), plan_id, ed, eu)


def plans_delete(plan_id: int):
    try:
        get_plans_service().delete(actor=_actor(), plan_id=plan_id)
        flash(_tr("تمت أرشفة الباقة. يمكنك استعادتها من سلة المحذوفات."), "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.plans_list"))
