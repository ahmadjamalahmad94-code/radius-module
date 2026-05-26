"""Subscriber Groups routes — CRUD UI.

URL prefix (under the radius blueprint): /admin/radius/subscriber-groups

Pattern reference: SERVICES_COOKBOOK §15.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session,
    url_for,
)

from ..core.errors import RadiusError
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import operations_repo, plans_repo, subscriber_groups_repo
from ..services.sessions import get_online_sessions_service
from ..services.users import get_users_service
from ..services.subscriber_groups import get_subscriber_groups_service
from .speed_rules_ui import handle_embedded_speed_rule, speed_rules_panel


def register_subscriber_groups_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/subscriber-groups", "subscriber_groups_list",
                    sg_list, methods=["GET"])
    bp.add_url_rule("/subscriber-groups/new", "subscriber_groups_new",
                    sg_new, methods=["GET"])
    bp.add_url_rule("/subscriber-groups", "subscriber_groups_create",
                    sg_create, methods=["POST"])
    bp.add_url_rule("/subscriber-groups/<int:gid>/edit",
                    "subscriber_groups_edit", sg_edit, methods=["GET"])
    bp.add_url_rule("/subscriber-groups/<int:gid>",
                    "subscriber_groups_update", sg_update, methods=["POST"])
    bp.add_url_rule("/subscriber-groups/<int:gid>/delete",
                    "subscriber_groups_delete", sg_delete, methods=["POST"])
    bp.add_url_rule("/subscriber-groups/<int:gid>/disconnect-online",
                    "subscriber_groups_disconnect_online",
                    sg_disconnect_online, methods=["POST"])
    bp.add_url_rule("/subscriber-groups/<int:gid>/quota/reset-daily",
                    "subscriber_groups_quota_reset_daily",
                    sg_quota_reset_daily, methods=["POST"])


# ────────────────────────── helpers ─────────────────────────────
def _tid() -> int:
    return session.get("tenant_id") or DEFAULT_TENANT_ID


def _actor() -> str:
    return session.get("admin_username") or "system"


def _form_to_kwargs() -> dict:
    f = request.form
    def _int_or_none(key: str):
        v = (f.get(key) or "").strip()
        try:
            return int(v) if v else None
        except ValueError:
            return None
    # connection_schedule comes from the access_schedule_picker partial
    # as JSON; working_days is the derived legacy CSV cache.
    sched_raw = (f.get("connection_schedule") or "").strip()
    try:
        from ..core.access_schedule import serialize, derive_working_days
        sched_clean = serialize(sched_raw) if sched_raw else ""
        derived_days = derive_working_days(sched_raw) if sched_raw else ""
    except Exception:  # noqa: BLE001
        sched_clean, derived_days = "", ""
    return {
        "name":                  (f.get("name") or "").strip(),
        "description":           (f.get("description") or "").strip(),
        "bandwidth_schedule_id": _int_or_none("bandwidth_schedule_id"),
        "default_plan_id":       _int_or_none("default_plan_id"),
        "default_auto_renewal":  bool(f.get("default_auto_renewal")),
        "working_days":          derived_days,
        "connection_schedule":   sched_clean,
    }


def _select_options(tid: int) -> dict:
    """Schedules + plans dropdowns. Both calls swallow errors so a broken
    sub-repo never breaks the form render."""
    try:
        schedules = operations_repo.list_bandwidth_schedules(tid)
    except Exception:  # noqa: BLE001
        schedules = []
    try:
        plans = list(plans_repo.list_plans(tid, limit=500))
    except Exception:  # noqa: BLE001
        plans = []
    return {"schedules": schedules, "plans": plans}


# ────────────────────────── views ───────────────────────────────
def sg_list():
    svc = get_subscriber_groups_service()
    items = svc.list(tenant_id=_tid())
    return render_template(
        "radius/subscriber_groups_list.html",
        items=items, count=len(items),
    )


def sg_new():
    opts = _select_options(_tid())
    return render_template(
        "radius/subscriber_groups_form.html",
        is_new=True, group=None, **opts,
        all_days=[("sat","السبت"),("sun","الأحد"),("mon","الإثنين"),
                  ("tue","الثلاثاء"),("wed","الأربعاء"),("thu","الخميس"),
                  ("fri","الجمعة")],
    )


def sg_create():
    kwargs = _form_to_kwargs()
    try:
        get_subscriber_groups_service().create(
            actor=_actor(), tenant_id=_tid(), **kwargs)
    except RadiusError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.subscriber_groups_new"))
    flash(f"تم إنشاء مجموعة «{kwargs['name']}»", "success")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_edit(gid: int):
    svc = get_subscriber_groups_service()
    g = svc.get(tenant_id=_tid(), gid=gid)
    if not g:
        abort(404)
    opts = _select_options(_tid())
    return render_template(
        "radius/subscriber_groups_form.html",
        is_new=False, group=g, **opts,
        members=svc.members(tenant_id=_tid(), gid=gid, limit=200),
        all_days=[("sat","السبت"),("sun","الأحد"),("mon","الإثنين"),
                  ("tue","الثلاثاء"),("wed","الأربعاء"),("thu","الخميس"),
                  ("fri","الجمعة")],
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="subscriber_group",
            subscriber_group_id=gid,
            return_to=request.path,
            title="قواعد سرعة المجموعة",
            help_text=(
                "كل قاعدة تطبق سرعة مختلفة في أوقات معينة على كل أعضاء "
                "هذه المجموعة. الأولوية الأقل تفوز عند التداخل."
            ),
        ),
    )


def sg_update(gid: int):
    # If the submit was a speed-rule action, handle it and short-circuit.
    if request.form.get("_speed_rule_action"):
        try:
            handle_embedded_speed_rule(
                tenant_id=_tid(),
                actor=_actor(),
                form=request.form,
                target_type="subscriber_group",
                subscriber_group_id=gid,
            )
            flash("تم تطبيق إجراء قاعدة السرعة.", "success")
        except RadiusError as e:
            flash(str(e), "error")
        return redirect(url_for("radius.subscriber_groups_edit", gid=gid))

    kwargs = _form_to_kwargs()
    try:
        get_subscriber_groups_service().update(
            actor=_actor(), tenant_id=_tid(), gid=gid, **kwargs)
    except RadiusError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.subscriber_groups_edit", gid=gid))
    flash("تم حفظ تعديلات المجموعة.", "success")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_delete(gid: int):
    get_subscriber_groups_service().delete(
        actor=_actor(), tenant_id=_tid(), gid=gid)
    flash("تم حذف المجموعة (تم فصل المشتركين عنها).", "info")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_disconnect_online(gid: int):
    group = subscriber_groups_repo.get(_tid(), gid)
    if not group:
        abort(404)
    member_names = set(subscriber_groups_repo.list_member_usernames(_tid(), gid))
    if not member_names:
        flash("لا يوجد أعضاء في هذه المجموعة.", "info")
        return redirect(url_for("radius.subscriber_groups_list"))

    disconnected = 0
    failed = 0
    try:
        sessions = get_online_sessions_service().list(limit=1000)
        for item in sessions:
            if item.username not in member_names:
                continue
            try:
                get_online_sessions_service().disconnect(
                    actor=_actor(),
                    username=item.username,
                    session_id=item.session_id,
                )
                disconnected += 1
            except RadiusError:
                failed += 1
    except RadiusError as e:
        flash(e.message or "تعذّر قراءة الجلسات المتصلة.", "error")
        return redirect(url_for("radius.subscriber_groups_list"))

    if disconnected:
        flash(f"تم إرسال أمر فصل {disconnected} جلسة من مجموعة «{group['name']}».", "success")
    elif failed:
        flash("تعذّر فصل جلسات المجموعة المتصلة.", "error")
    else:
        flash(f"لا توجد جلسات متصلة حالياً لمجموعة «{group['name']}».", "info")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_quota_reset_daily(gid: int):
    group = subscriber_groups_repo.get(_tid(), gid)
    if not group:
        abort(404)
    usernames = subscriber_groups_repo.list_member_usernames(_tid(), gid)
    if not usernames:
        flash("لا يوجد أعضاء في هذه المجموعة.", "info")
        return redirect(url_for("radius.subscriber_groups_list"))

    reset_count = 0
    failed = 0
    svc = get_users_service()
    for username in usernames:
        try:
            svc.reset_daily_quota(actor=_actor(), username=username)
            reset_count += 1
        except RadiusError:
            failed += 1

    if reset_count:
        flash(f"تمت استعادة الكوتة اليومية لـ {reset_count} مشترك في مجموعة «{group['name']}».", "success")
    if failed:
        flash(f"تعذّرت استعادة الكوتة لـ {failed} مشترك.", "error")
    return redirect(url_for("radius.subscriber_groups_list"))
