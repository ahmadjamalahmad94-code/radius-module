"""Plans (العروض) routes — CRUD."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import PLAN_TYPES
from ..core.errors import RadiusError
from ..core.types import AccessPlan
from ..services.plans import get_plans_service


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


def _form_to_dto(*, plan_id: int | None = None) -> AccessPlan:
    days_raw = request.form.getlist("allowed_days") or ["mon","tue","wed","thu","fri","sat","sun"]
    return AccessPlan(
        id=plan_id,
        name=(request.form.get("name") or "").strip(),
        code=(request.form.get("code") or "").strip(),
        plan_type=(request.form.get("plan_type") or "time").strip().lower(),
        duration_minutes=_i("duration_minutes"),
        validity_days=_i("validity_days"),
        max_daily_minutes=_i("max_daily_minutes"),
        max_weekly_minutes=_i("max_weekly_minutes"),
        max_monthly_minutes=_i("max_monthly_minutes"),
        quota_total_mb=_i("quota_total_mb"),
        quota_daily_mb=_i("quota_daily_mb"),
        quota_monthly_mb=_i("quota_monthly_mb"),
        quota_reset_strategy=(request.form.get("quota_reset_strategy") or "rolling"),
        speed_up_kbps=_i("speed_up_kbps"),
        speed_down_kbps=_i("speed_down_kbps"),
        burst_up_kbps=_i("burst_up_kbps"),
        burst_down_kbps=_i("burst_down_kbps"),
        burst_threshold_kbps=_i("burst_threshold_kbps"),
        burst_time_sec=_i("burst_time_sec"),
        concurrent_sessions=max(1, _i("concurrent_sessions", 1)),
        session_timeout_sec=_i("session_timeout_sec"),
        idle_timeout_sec=_i("idle_timeout_sec"),
        address_pool=(request.form.get("address_pool") or "").strip(),
        framed_pool=(request.form.get("framed_pool") or "").strip(),
        vlan_id=_i("vlan_id"),
        ipv6_pool=(request.form.get("ipv6_pool") or "").strip(),
        bind_mac=bool(request.form.get("bind_mac")),
        bind_ip=bool(request.form.get("bind_ip")),
        allowed_days=tuple(days_raw),
        allowed_hours_from=(request.form.get("allowed_hours_from") or "").strip(),
        allowed_hours_to=(request.form.get("allowed_hours_to") or "").strip(),
        price=_f("price"),
        currency=(request.form.get("currency") or "JOD").strip(),
        description=(request.form.get("description") or "").strip(),
        enabled=bool(request.form.get("enabled")),
        priority=_i("priority", 100),
        color=(request.form.get("color") or "#F4BA2A").strip(),
    )


# ─────────────── views ───────────────

def plans_list():
    items = get_plans_service().list(limit=500)
    return render_template("radius/plans_list.html", items=items)


def plans_new():
    return render_template("radius/plans_form.html",
        plan=AccessPlan(id=None, name="", enabled=True),
        plan_types=PLAN_TYPES, is_new=True)


def plans_create():
    dto = _form_to_dto()
    try:
        saved = get_plans_service().create(actor=_actor(), plan=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/plans_form.html",
            plan=dto, plan_types=PLAN_TYPES, is_new=True), 400
    flash(f"تم إنشاء العرض «{saved.name}».", "success")
    return redirect(url_for("radius.plans_list"))


def plans_edit(plan_id: int):
    try:
        plan = get_plans_service().get(plan_id)
    except RadiusError:
        abort(404)
    return render_template("radius/plans_form.html",
        plan=plan, plan_types=PLAN_TYPES, is_new=False)


def plans_update(plan_id: int):
    dto = _form_to_dto(plan_id=plan_id)
    try:
        saved = get_plans_service().update(actor=_actor(), plan=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/plans_form.html",
            plan=dto, plan_types=PLAN_TYPES, is_new=False), 400
    flash(f"تم تحديث «{saved.name}».", "success")
    return redirect(url_for("radius.plans_list"))


def plans_delete(plan_id: int):
    try:
        get_plans_service().delete(actor=_actor(), plan_id=plan_id)
        flash("تم حذف العرض.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.plans_list"))
