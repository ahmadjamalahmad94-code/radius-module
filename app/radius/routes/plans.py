"""Plans (العروض) routes — CRUD.

RM-H3: extended with full AdvRadius scope.
Hybrid storage:
  - الحقول queryable كأعمدة DB حقيقية (migration 012).
  - الحقول vendor-specific / المتقدمة في metadata JSON مُجمَّع
    {general, subscription, advanced, mikrotik, notifications}.
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import PLAN_TYPES
from ..core.errors import RadiusError
from ..core.types import AccessPlan
from ..services.plans import get_plans_service


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
    bp.add_url_rule("/plans/<int:plan_id>/delete", "plans_delete", plans_delete, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _i(name: str, default: int = 0) -> int:
    try:
        return int(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float = 0.0) -> float:
    try:
        return float(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _b(name: str) -> bool:
    return request.form.get(name, "") in ("1", "on", "true", "yes")


def _s(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _form_to_dto(*, plan_id: int | None = None) -> AccessPlan:
    days_raw = request.form.getlist("allowed_days") or ["mon","tue","wed","thu","fri","sat","sun"]

    # metadata: collect flat from form, group into JSON
    flat_meta = {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if v:
            flat_meta[mf] = v
    meta_json = json.dumps(_flat_to_grouped(flat_meta), ensure_ascii=False)

    return AccessPlan(
        id=plan_id,
        name=_s("name"),
        code=_s("code"),
        plan_type=_s("plan_type").lower() or "time",
        service_type=_s("service_type") or "Hotspot",
        duration_minutes=_i("duration_minutes"),
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
        currency=_s("currency") or "JOD",
        description=_s("description"),
        enabled=_b("enabled"),
        priority=_i("priority", 100),
        color=_s("color") or "#F4BA2A",
        # RM-H3 fields
        speed_control_enabled=_b("speed_control_enabled"),
        cir_down_kbps=_i("cir_down_kbps"),
        cir_up_kbps=_i("cir_up_kbps"),
        burst_enabled=_b("burst_enabled"),
        nightly_unlimited_enabled=_b("nightly_unlimited_enabled"),
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
        hotspot_enabled=_b("hotspot_enabled"),
        ppp_enabled=_b("ppp_enabled"),
        offer_hours_from=_s("offer_hours_from"),
        offer_hours_to=_s("offer_hours_to"),
        metadata=meta_json,
    )


# ─────────────── views ───────────────

def plans_list():
    items = get_plans_service().list(limit=500)
    return render_template("radius/plans_list.html", items=items)


def plans_new():
    empty = AccessPlan(id=None, name="", enabled=True)
    return render_template("radius/plans_form.html",
        plan=_plan_with_meta_for_template(empty),
        plan_types=PLAN_TYPES, is_new=True)


def plans_create():
    dto = _form_to_dto()
    try:
        saved = get_plans_service().create(actor=_actor(), plan=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/plans_form.html",
            plan=_plan_with_meta_for_template(dto), plan_types=PLAN_TYPES, is_new=True), 400
    flash(f"تم إنشاء العرض «{saved.name}».", "success")
    return redirect(url_for("radius.plans_list"))


def plans_edit(plan_id: int):
    try:
        plan = get_plans_service().get(plan_id)
    except RadiusError:
        abort(404)
    return render_template("radius/plans_form.html",
        plan=_plan_with_meta_for_template(plan),
        plan_types=PLAN_TYPES, is_new=False)


def plans_update(plan_id: int):
    dto = _form_to_dto(plan_id=plan_id)
    try:
        saved = get_plans_service().update(actor=_actor(), plan=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/plans_form.html",
            plan=_plan_with_meta_for_template(dto), plan_types=PLAN_TYPES, is_new=False), 400
    flash(f"تم تحديث «{saved.name}».", "success")
    return redirect(url_for("radius.plans_list"))


def plans_delete(plan_id: int):
    try:
        get_plans_service().delete(actor=_actor(), plan_id=plan_id)
        flash("تم حذف العرض.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.plans_list"))
