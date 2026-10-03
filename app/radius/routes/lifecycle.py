"""Web UI routes for lifecycle retention policies."""
from __future__ import annotations
from app.i18n_text import N_, _tr

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..services import lifecycle


def register_lifecycle_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/lifecycle", "lifecycle_settings", lifecycle_settings, methods=["GET"])
    bp.add_url_rule("/lifecycle/policies", "lifecycle_policy_create",
                    lifecycle_policy_create, methods=["POST"])
    bp.add_url_rule("/lifecycle/policies/<int:policy_id>/disable",
                    "lifecycle_policy_disable", lifecycle_policy_disable, methods=["POST"])
    bp.add_url_rule("/lifecycle/run", "lifecycle_run", lifecycle_run, methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "admin"


def lifecycle_settings():
    policies = lifecycle.list_policies(_tid())
    preview = lifecycle.preview(_tid(), limit=500)
    return render_template(
        "radius/lifecycle.html",
        policies=policies,
        preview=preview,
        entity_options=[
            ("card", N_("بطاقات")),
            ("subscriber", N_("مشتركين")),
            ("card_batch", N_("حزم بطاقات")),
            ("external_file", N_("ملفات خارجية")),
        ],
        unit_options=[
            ("minutes", N_("دقائق")),
            ("hours", N_("ساعات")),
            ("days", N_("أيام")),
            ("months", N_("أشهر")),
        ],
    )


def lifecycle_policy_create():
    payload = {
        "entity_type": request.form.get("entity_type") or "card",
        "trigger_type": request.form.get("trigger_type") or "expired_at",
        "delay_value": request.form.get("delay_value") or 0,
        "delay_unit": request.form.get("delay_unit") or "days",
        "action": "archive",
        "retention_value": request.form.get("retention_value") or 90,
        "retention_unit": request.form.get("retention_unit") or "days",
        "enabled": request.form.get("enabled") == "1",
    }
    try:
        lifecycle.create_policy(_tid(), payload, actor=_actor())
        flash(_tr("تم حفظ سياسة الأرشفة التلقائية."), "success")
    except lifecycle.LifecycleValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.lifecycle_settings"))


def lifecycle_policy_disable(policy_id: int):
    policy = lifecycle.disable_policy(_tid(), policy_id, actor=_actor())
    if policy:
        flash(_tr("تم تعطيل سياسة الأرشفة."), "success")
    else:
        flash(_tr("السياسة غير موجودة."), "error")
    return redirect(url_for("radius.lifecycle_settings"))


def lifecycle_run():
    result = lifecycle.run(_tid(), actor=_actor(), limit=500)
    if result.get("failed"):
        flash(_tr("تم تشغيل الأرشفة مع وجود عناصر فشلت. راجع السجل."), "warning")
    else:
        flash(_tr('تمت الأرشفة الآمنة لعدد %(v)s عنصر.', v=result.get('changed', 0)), "success")
    return redirect(url_for("radius.lifecycle_settings"))
