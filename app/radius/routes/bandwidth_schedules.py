"""Web UI for time-based bandwidth schedules."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.operations import get_operations_service
from ..services.plans import get_plans_service


def register_bandwidth_schedule_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/bandwidth-schedules",
        "bandwidth_schedules",
        bandwidth_schedules,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/bandwidth-schedules",
        "bandwidth_schedules_create",
        bandwidth_schedules_create,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/bandwidth-schedules/<int:schedule_id>/apply",
        "bandwidth_schedules_apply",
        bandwidth_schedules_apply,
        methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _payload() -> dict:
    enabled = request.form.get("enabled") in {"1", "true", "on", "yes"}
    return {
        "plan_id": request.form.get("plan_id"),
        "name": request.form.get("name"),
        "starts_at_time": request.form.get("starts_at_time"),
        "ends_at_time": request.form.get("ends_at_time"),
        "speed_down_kbps": request.form.get("speed_down_kbps") or 0,
        "speed_up_kbps": request.form.get("speed_up_kbps") or 0,
        "cir_down_kbps": request.form.get("cir_down_kbps") or 0,
        "cir_up_kbps": request.form.get("cir_up_kbps") or 0,
        "restore_mode": request.form.get("restore_mode") or "profile_default",
        "enabled": enabled,
        "notes": request.form.get("notes") or "",
    }


def _plans() -> list:
    return list(get_plans_service().list(limit=500))


def bandwidth_schedules():
    svc = get_operations_service()
    schedules = svc.list_bandwidth_schedules(tenant_id=_tid(), limit=500)
    plans = _plans()
    return render_template(
        "radius/bandwidth_schedules.html",
        schedules=schedules,
        plans=plans,
        plan_names={plan.id: plan.name for plan in plans},
        apply_result=None,
    )


def bandwidth_schedules_create():
    try:
        get_operations_service().create_bandwidth_schedule(
            tenant_id=_tid(),
            actor=_actor(),
            data=_payload(),
        )
        flash("تم حفظ جدول السرعة. التطبيق على RADIUS ما زال غير مباشر في هذه المرحلة.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.bandwidth_schedules"))


def bandwidth_schedules_apply(schedule_id: int):
    try:
        result = get_operations_service().apply_bandwidth_schedule(
            tenant_id=_tid(),
            schedule_id=schedule_id,
            actor=_actor(),
        )
        if result.get("applied_to_radius"):
            flash("تم تطبيق الجدول على RADIUS.", "success")
        else:
            flash("تم تنفيذ تجربة تطبيق فقط. لم يتم تغيير السرعة فعليًا على RADIUS.", "warning")
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.bandwidth_schedules"))
