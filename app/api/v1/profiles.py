"""
Profiles (Plans) endpoints — full CUD parity for the Flutter client.

Every write delegates to `PlansService` (the same code path the Flask web
form uses), so validation, audit, and adapter sync stay identical across
clients. The Flask web admin form is untouched.

Field intake is a whitelist mirroring `AccessPlan`. `metadata` is accepted
as either a dict or a JSON string and stored as a string. The response
serialiser parses metadata back to a dict so Flutter doesn't have to.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusConflict, RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.core.strict_input import parse_strict_bool
from ...radius.services.plans import plan_field_label
from ...radius.core.types import AccessPlan
from ...radius.services.license_admin_capacity import (
    CapacityEnforcementService,
    capacity_error_response,
)
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


# Whitelist of patchable / creatable plan fields. `id`, `tenant_id`,
# `created_at`, `updated_at` are not editable here.
_STR_FIELDS = (
    "name", "code", "plan_type", "service_type", "typebp", "limit_type",
    "duration_unit", "validity_unit", "data_unit",
    "quota_reset_strategy", "burst_raw",
    "address_pool", "framed_pool", "ipv6_pool",
    "allowed_hours_from", "allowed_hours_to",
    "on_login", "on_logout",
    "currency", "plan_tier", "project", "description", "color",
    "offer_hours_from", "offer_hours_to",
    "service_scope",
)
_INT_FIELDS = (
    "duration_value", "duration_minutes",
    "validity_value", "validity_days",
    "max_daily_minutes", "max_weekly_minutes", "max_monthly_minutes",
    "session_timeout_sec", "idle_timeout_sec",
    "data_value",
    "quota_total_mb", "quota_daily_mb", "quota_monthly_mb",
    "bandwidth_id",
    "speed_up_kbps", "speed_down_kbps",
    "burst_up_kbps", "burst_down_kbps", "burst_threshold_kbps", "burst_time_sec",
    "concurrent_sessions", "pool_id", "vlan_id", "allowed_devices_count",
    "priority",
    # RM-H3
    "cir_down_kbps", "cir_up_kbps",
    "monthly_download_quota_mb", "monthly_upload_quota_mb", "monthly_combined_quota_mb",
    "daily_download_quota_mb", "daily_upload_quota_mb", "daily_combined_quota_mb",
    "max_consumption_times", "ticket_validity_days", "working_hours_limit",
    "max_loan_minutes",
)
_FLOAT_FIELDS = ("price_card", "price_bulk", "price")
_BOOL_FIELDS = (
    "bind_mac", "bind_ip", "force_mac_address",
    "auto_renew", "prepaid", "enabled",
    # RM-H3
    "speed_control_enabled", "burst_enabled", "nightly_unlimited_enabled",
    "single_use_once", "hotspot_enabled", "ppp_enabled",
    "loan_enabled", "speed_override_allowed",
    # «بلا حدّ للسرعة» (هجرة 172) — كان الويب وحده يقبله، فالتطبيق/الـ API لا
    # يستطيع إنشاء باقةٍ مفتوحة السرعة (الصفر يُرفض بلا هذا العلَم).
    "speed_unlimited", "shared_single_session",
)
_TUPLE_FIELDS = ("allowed_days", "router_ids")

_VALID_DAYS = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}


def _normalize_metadata(raw) -> str:
    """Accept either a dict/list or a JSON string, always store a JSON string.

    Invalid input raises RadiusValidationError so the caller can return 422
    and the request fails atomically — silently normalising to "{}" would
    wipe existing metadata on PATCH (destructive).
    """
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        if not raw.strip():
            return "{}"
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as e:
            raise RadiusValidationError("بيانات metadata ليست JSON صالحًا.")
        if not isinstance(parsed, (dict, list)):
            raise RadiusValidationError(
                "بيانات metadata يجب أن تتحول إلى كائن أو قائمة JSON.")
        return raw
    if isinstance(raw, (dict, list)):
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise RadiusValidationError("تعذّر تحويل metadata إلى JSON.")
    raise RadiusValidationError(
        f"metadata يجب أن تكون قاموسًا أو قائمة أو نص JSON، والقيمة الحالية من نوع {type(raw).__name__}.")


def _coerce_int(name: str, v: Any) -> int:
    """عددٌ صحيح من الجسم. يرفض ``true`` (كانت تُخزَّن 1) والكسور (90.7 كانت
    تصير 90) والنصّ غير الرقميّ والقيم الضخمة (10^20 و2^63 كانت 500 من
    SQLite) — كلّها 422 عربيّ باسم الحقل المقروء. المدى الدقيق لكلّ حقل
    (سالب/سقف) يفحصه ``plans._validate`` المشترك مع الويب."""
    from ...radius.services.plans import PLAN_INT_MAX, plan_field_label
    label = plan_field_label(name)
    if v in (None, ""):
        return 0
    if isinstance(v, bool) or isinstance(v, (dict, list, tuple)):
        raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")) or not v.is_integer():
            raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
    try:
        out = int(v if not isinstance(v, str) else v.strip())
    except (TypeError, ValueError, OverflowError):
        raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
    if abs(out) > PLAN_INT_MAX:
        raise RadiusValidationError(f"قيمة «{label}» أكبر من المسموح.")
    return out


from ...radius.core.numbers import NonFiniteNumber, strict_float  # noqa: E402


def _coerce_float(name: str, v: Any) -> float:
    if v in (None, ""):
        return 0.0
    if isinstance(v, bool):
        raise RadiusValidationError(f"قيمة «{plan_field_label(name)}» يجب أن تكون رقمية.")
    try:
        return strict_float(v, name) + 0.0  # ‎-0.0 → 0.0
    except NonFiniteNumber:
        raise
    except (TypeError, ValueError):
        raise RadiusValidationError(f"قيمة «{plan_field_label(name)}» يجب أن تكون رقمية.")


def _coerce_days(v: Any) -> tuple[str, ...]:
    """Accept list/tuple of day codes or CSV string. Validates against
    {sun..sat}. Empty input → empty tuple (caller decides default)."""
    if v in (None, ""):
        return ()
    if isinstance(v, str):
        parts = [p.strip().lower() for p in v.split(",") if p.strip()]
    elif isinstance(v, (list, tuple)):
        parts = [str(p).strip().lower() for p in v if str(p).strip()]
    else:
        raise RadiusValidationError("الأيام المسموحة يجب أن تكون قائمة أو نصًا مفصولًا بفواصل.")
    bad = [p for p in parts if p not in _VALID_DAYS]
    if bad:
        raise RadiusValidationError(
            "الأيام المسموحة تحتوي قيمًا غير صحيحة: " + "، ".join(str(b) for b in bad)
            + " (المسموح: sun, mon, tue, wed, thu, fri, sat).")
    # de-dup, preserve canonical order
    canonical_order = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    seen = set(parts)
    return tuple(d for d in canonical_order if d in seen)


def _coerce_router_ids(v: Any) -> tuple[int, ...]:
    if v in (None, ""):
        return ()
    if isinstance(v, str):
        parts = [p.strip() for p in v.split(",") if p.strip()]
    elif isinstance(v, (list, tuple)):
        parts = [str(p).strip() for p in v if str(p).strip() != ""]
    else:
        raise RadiusValidationError("أرقام الراوترات يجب أن تكون قائمة أو نصًا مفصولًا بفواصل.")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except (TypeError, ValueError):
            raise RadiusValidationError(f"قيمة غير رقمية في أرقام الراوترات: {p!r}")
    return tuple(out)


def _apply_body(plan: AccessPlan, body: dict) -> AccessPlan:
    changes: dict = {}
    for k in _STR_FIELDS:
        if k in body:
            v = body[k]
            changes[k] = "" if v is None else str(v)
    for k in _INT_FIELDS:
        if k in body:
            changes[k] = _coerce_int(k, body[k])
    # priority: 1–10 like the web (F04 N-L10). 0/null and 100 — the old API /
    # app defaults — mean «not chosen» ⇒ 5 (plans._normalize); anything else
    # outside 1–10 is a 422 from plans._validate.
    if int(changes.get("data_value") or 0) > 0 and int(changes.get("data_value") or 0) != int(
            getattr(plan, "data_value", 0) or 0):
        # F04 N-L11: data_value/data_unit were stored and never enforced nor shown
        # (has_quota false) — a «2 GB» plan was unlimited. Refused, not guessed:
        # the enforced caps are quota_total_mb / quota_daily_mb / quota_monthly_mb
        # (+ per-direction). An unchanged stored value (old rows) passes.
        raise RadiusValidationError(
            "حقل «حجم البيانات» (data_value) غير مُطبَّق — استخدم «quota_total_mb» "
            "للكوتة الإجماليّة بالميجابايت (أو quota_daily_mb / quota_monthly_mb).")
    for k in _FLOAT_FIELDS:
        if k in body:
            changes[k] = _coerce_float(k, body[k])
    for k in _BOOL_FIELDS:
        if k in body:
            # 🔴 ‏bool("false") صحيحٌ في بايثون — كانت "false"/"0"/"no" تُخزَّن
            # «مفعّل». المحلّل الصارم المشترك يقبل الصيغ المعروفة ويرفض الباقي.
            changes[k] = parse_strict_bool(body[k], label=plan_field_label(k))
    if "allowed_days" in body:
        days = _coerce_days(body["allowed_days"])
        # Service expects at least one day; treat empty as "all 7".
        if not days:
            days = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
        changes["allowed_days"] = days
    if "router_ids" in body:
        changes["router_ids"] = _coerce_router_ids(body["router_ids"])
    if "metadata" in body:
        changes["metadata"] = _normalize_metadata(body["metadata"])
    if "connection_schedule" in body:
        changes["connection_schedule"] = _coerce_connection_schedule(
            body["connection_schedule"])
    if "service_scope" in body:
        # service_scope مشتقّ من service_type في الخدمة (مثل الويب). نطاقٌ غير
        # صالح يبقى 422؛ ونطاقٌ وحده (بلا service_type) يُترجَم إلى نوع الخدمة
        # المقابل بدل أن يُتجاهَل بصمت.
        from ...radius.services.operations import validate_service_scope
        scope = validate_service_scope(str(body["service_scope"] or ""))
        if "service_type" not in body:
            changes["service_type"] = {"hotspot": "Hotspot", "broadband": "PPPoE",
                                       "both": "Both"}[scope]
    return replace(plan, **changes)


def _coerce_connection_schedule(v: Any) -> str:
    """«الأيام والأوقات المسموحة» (مُطبَّق) — نفس تحقّق الويب
    (access_schedule.parse/serialize)، لكن المدخل غير الصالح 422 لا تفريغٌ صامت.
    يقبل نصّ JSON أو كائنًا ``{"windows": [...]}``؛ فارغ/null ⇒ بلا قيد."""
    from ...radius.core.access_schedule import AccessScheduleError, serialize
    if v is None or (isinstance(v, str) and not v.strip()):
        return ""
    if not isinstance(v, (str, dict)):
        raise RadiusValidationError(
            "«الأيام والأوقات المسموحة» (connection_schedule) يجب أن تكون كائن JSON "
            "بالشكل {\"windows\": [...]}.")
    try:
        return serialize(v)
    except AccessScheduleError as e:
        raise RadiusValidationError(
            f"«الأيام والأوقات المسموحة» (connection_schedule) غير صالحة: {e}")
    except (TypeError, ValueError, AttributeError):
        raise RadiusValidationError(
            "«الأيام والأوقات المسموحة» (connection_schedule) غير صالحة.")


def _serialize(plan: AccessPlan) -> dict:
    d = asdict(plan)
    for k in ("created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat() + "Z"
    # Tuples → lists for JSON cleanliness.
    if isinstance(d.get("allowed_days"), tuple):
        d["allowed_days"] = list(d["allowed_days"])
    if isinstance(d.get("router_ids"), tuple):
        d["router_ids"] = list(d["router_ids"])
    # Derived (read-only, fix2): the plan's pricing period (duration, else
    # validity, else a 30-day month — same basis as payments/extend) and its
    # price per minute. The change-plan picker must compare ``rate_per_minute``
    # (70/30 days is dearer per day than 5/1 day), never the total price.
    from ...radius.services.users import plan_period_minutes, plan_rate_per_minute
    d["period_minutes"] = int(plan_period_minutes(plan))
    d["rate_per_minute"] = round(plan_rate_per_minute(plan), 8)
    # Metadata string → parsed dict for client convenience.
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            d["metadata"] = json.loads(meta or "{}")
        except (TypeError, ValueError):
            d["metadata"] = {}
    return d


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/profiles", "profiles_list",
                    require_api_token(profiles_list), methods=["GET"])
    bp.add_url_rule("/profiles", "profiles_create",
                    require_api_token(profiles_create), methods=["POST"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_get",
                    require_api_token(profiles_get), methods=["GET"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_patch",
                    require_api_token(profiles_patch), methods=["PATCH"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_delete",
                    require_api_token(profiles_delete), methods=["DELETE"])
    # fix3 integration: a lightweight picker list for the create forms — readable
    # with plans.view OR the permission of the form that needs it (users.create /
    # cards.generate), so a manager without «عرض الباقات» can still pick a plan.
    bp.add_url_rule("/plans/options", "plans_options",
                    require_api_token(plans_options), methods=["GET"])
    bp.add_url_rule("/profiles/options", "profiles_options",
                    require_api_token(plans_options), methods=["GET"])


def _svc():
    from ...radius.services.plans import get_plans_service
    return get_plans_service()


# ─────────────── views ───────────────

def profiles_list():
    from .paging import PagingError, page_args
    try:
        limit, offset = page_args(default=200, maximum=1000)
    except PagingError as e:
        return fail("validation_error", e.message, status=422)
    items = _svc().list(limit=limit, offset=offset)
    return ok({"items": [_serialize(p) for p in items], "count": len(items)})


def plan_option(plan: AccessPlan, system_currency: str) -> dict:
    """One picker row: only what a create form needs (no speeds/quotas/metadata)."""
    from ...radius.services.users import plan_period_minutes
    return {
        "id": plan.id,
        "name": plan.name,
        "price": float(plan.price or 0),
        "currency": (plan.currency or "").strip().upper() or system_currency,
        "duration_minutes": int(plan.duration_minutes or 0),
        "duration_value": int(plan.duration_value or 0),
        "duration_unit": plan.duration_unit or "",
        "validity_days": int(plan.validity_days or 0),
        "period_minutes": int(plan_period_minutes(plan)),
        "plan_type": plan.plan_type or "",
    }


def plans_options():
    """``GET /api/v1/plans/options`` (alias ``/profiles/options``) — active plans
    (enabled, not archived) as ``{id, name, price, currency, duration…}``.

    Guard: ``plans.view`` OR ``users.create`` OR ``cards.generate`` (the same web
    decisions as the plans page / create form / generate form)."""
    from ...radius.core.system_config import default_currency
    system_currency = (default_currency() or "").strip().upper()
    items = [plan_option(p, system_currency) for p in _svc().list(limit=1000)
             if getattr(p, "enabled", True) and getattr(p, "deleted_at", None) is None]
    items.sort(key=lambda r: (str(r["name"] or "").lower(), r["id"] or 0))
    return ok({"items": items, "count": len(items), "currency": system_currency})


def profiles_get(profile_id: int):
    try:
        plan = _svc().get(profile_id)
    except RadiusNotFound:
        return fail("not_found", f"الباقة {profile_id} غير موجودة.", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(plan))


def profiles_create():
    from ..json_input import json_object
    body, err = json_object()
    if err is not None:
        return err
    if not isinstance(body.get("name"), str) or not body["name"].strip():
        return fail("validation_error", "اسم الباقة مطلوب.", status=422)
    capacity = CapacityEnforcementService().check_create(
        tenant_id=_tid(),
        feature_key="profiles",
        limit_path="profiles.max_total",
        usage_metric="profiles_plans_count",
    )
    if not capacity.allowed:
        return capacity_error_response(capacity)

    # Seed a minimal plan, then apply body fields.
    seed = AccessPlan(
        id=None,
        tenant_id=_tid(),
        name=str(body["name"]).strip(),
    )
    try:
        plan = _apply_body(seed, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        saved = _svc().create(actor=_actor(), plan=plan)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(saved), status=201)


def profiles_patch(profile_id: int):
    # نصٌّ غير JSON كان يمرّ «200» بلا أيّ تغيير — الآن 422 (json_object).
    from ..json_input import json_object
    body, err = json_object()
    if err is not None:
        return err
    try:
        existing = _svc().get(profile_id)
    except RadiusNotFound:
        return fail("not_found", f"الباقة {profile_id} غير موجودة.", status=404)
    try:
        new_plan = _apply_body(existing, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        _svc().update(actor=_actor(), plan=new_plan)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(_svc().get(profile_id)))


def profiles_delete(profile_id: int):
    # Adapter delete is silent on missing rows, so check existence first to
    # give callers a clean 404 instead of a misleading 200.
    try:
        _svc().get(profile_id)
    except RadiusNotFound:
        return fail("not_found", f"الباقة {profile_id} غير موجودة.", status=404)
    try:
        _svc().delete(actor=_actor(), plan_id=profile_id)
    except RadiusConflict as e:
        # الباقة عليها مشتركون/حزم/بطاقات — العدد في details.
        return fail("plan_in_use", e.message, status=409, details=e.details)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": profile_id, "archived": True})
