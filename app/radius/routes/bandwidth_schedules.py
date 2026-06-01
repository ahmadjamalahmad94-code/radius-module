"""Web UI for time-based bandwidth schedules."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service
from ..services.plans import get_plans_service
from ..services.users import get_users_service


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
        "target_type": request.form.get("target_type") or "plan",
        "plan_id": request.form.get("plan_id"),
        "subscriber_username": request.form.get("subscriber_username") or "",
        "card_batch_id": request.form.get("card_batch_id") or "",
        "priority": request.form.get("priority") or 100,
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


def _safe_return_url() -> str:
    value = (request.form.get("return_to") or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("radius.bandwidth_schedules")


def _payload_from_saved_schedule(base: dict) -> dict:
    source_id = request.form.get("source_schedule_id")
    if not source_id:
        return base
    try:
        source_id_i = int(source_id)
    except (TypeError, ValueError):
        return base

    source = get_operations_service().get_bandwidth_schedule(
        tenant_id=_tid(),
        schedule_id=source_id_i,
    )
    if not source:
        return base

    copied = {
        "name": request.form.get("name") or f"نسخة من {source.get('name') or 'جدول محفوظ'}",
        "target_type": base.get("target_type"),
        "plan_id": base.get("plan_id"),
        "subscriber_username": base.get("subscriber_username"),
        "card_batch_id": base.get("card_batch_id"),
        "priority": request.form.get("priority") or source.get("priority") or 100,
        "starts_at_time": source.get("starts_at_time"),
        "ends_at_time": source.get("ends_at_time"),
        "speed_down_kbps": source.get("speed_down_kbps") or 0,
        "speed_up_kbps": source.get("speed_up_kbps") or 0,
        "cir_down_kbps": source.get("cir_down_kbps") or 0,
        "cir_up_kbps": source.get("cir_up_kbps") or 0,
        "restore_mode": source.get("restore_mode") or "profile_default",
        "enabled": base.get("enabled", True),
        "notes": request.form.get("notes") or source.get("notes") or "",
        "metadata": {
            "copied_from_schedule_id": source_id_i,
            "copied_from_target_type": source.get("target_type"),
        },
    }
    return copied


def _plans() -> list:
    return list(get_plans_service().list(limit=500))


def _subscribers() -> list:
    return list(get_users_service().list(user_type="subscriber", limit=500))


def _batches() -> list:
    return list(get_cards_service().list_batches(limit=500))


def bandwidth_schedules():
    svc = get_operations_service()
    schedules = svc.list_bandwidth_schedules(tenant_id=_tid(), limit=500)
    plans = _plans()
    subscribers = _subscribers()
    batches = _batches()
    return render_template(
        "radius/bandwidth_schedules.html",
        schedules=schedules,
        plans=plans,
        subscribers=subscribers,
        batches=batches,
        plan_names={plan.id: plan.name for plan in plans},
        subscriber_names={sub.username: (sub.full_name or sub.username) for sub in subscribers},
        batch_names={batch.id: f"{batch.batch_code} - {batch.package_name or batch.service_name or 'بدون اسم'}" for batch in batches},
        apply_result=None,
    )


def bandwidth_schedules_create():
    try:
        payload = _payload_from_saved_schedule(_payload())
        get_operations_service().create_bandwidth_schedule(
            tenant_id=_tid(),
            actor=_actor(),
            data=payload,
        )
        flash("تم حفظ جدول السرعة. التطبيق على RADIUS ما زال غير مباشر في هذه المرحلة.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(_safe_return_url())


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
            flash("تم تنفيذ فحص جاهزية فقط. لم يتم تغيير السرعة فعليًا على RADIUS.", "warning")
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.bandwidth_schedules"))
