"""Subscriber Groups routes — CRUD UI.

URL prefix (under the radius blueprint): /admin/radius/subscriber-groups

Pattern reference: SERVICES_COOKBOOK §15.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

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
        "default_plan_id":       _int_or_none("default_plan_id"),
        "default_auto_renewal":  bool(f.get("default_auto_renewal")),
        "working_days":          derived_days,
        "connection_schedule":   sched_clean,
    }


def _select_options(tid: int) -> dict:
    """Plans dropdown (swallows errors so a broken sub-repo never breaks the
    form render). fields-sched: the group «جدولة السرعة» select is gone, so
    no schedule list is loaded any more."""
    try:
        plans = list(plans_repo.list_plans(tid, limit=500))
    except Exception:  # noqa: BLE001
        plans = []
    return {"plans": plans}


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
        all_days=[("sat",N_("السبت")),("sun",N_("الأحد")),("mon",N_("الإثنين")),
                  ("tue",N_("الثلاثاء")),("wed",N_("الأربعاء")),("thu",N_("الخميس")),
                  ("fri",N_("الجمعة"))],
    )


def sg_create():
    kwargs = _form_to_kwargs()
    try:
        get_subscriber_groups_service().create(
            actor=_actor(), tenant_id=_tid(), **kwargs)
    except RadiusError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.subscriber_groups_new"))
    flash(_tr('تم إنشاء مجموعة «%(name)s»', name=kwargs['name']), "success")
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
        all_days=[("sat",N_("السبت")),("sun",N_("الأحد")),("mon",N_("الإثنين")),
                  ("tue",N_("الثلاثاء")),("wed",N_("الأربعاء")),("thu",N_("الخميس")),
                  ("fri",N_("الجمعة"))],
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="subscriber_group",
            subscriber_group_id=gid,
            return_to=request.path,
            title=N_("قواعد سرعة المجموعة"),
            help_text=(
                N_("كل قاعدة تطبق سرعة مختلفة في أوقات معينة على كل أعضاء "
                "هذه المجموعة. الأولوية الأقل تفوز عند التداخل.")
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
            flash(_tr("تم تطبيق إجراء قاعدة السرعة."), "success")
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
    flash(_tr("تم حفظ تعديلات المجموعة."), "success")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_delete(gid: int):
    get_subscriber_groups_service().delete(
        actor=_actor(), tenant_id=_tid(), gid=gid)
    flash(_tr("تم حذف المجموعة (تم فصل المشتركين عنها)."), "info")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_disconnect_online(gid: int):
    group = subscriber_groups_repo.get(_tid(), gid)
    if not group:
        abort(404)
    member_names = set(subscriber_groups_repo.list_member_usernames(_tid(), gid))
    if not member_names:
        flash(_tr("لا يوجد أعضاء في هذه المجموعة."), "info")
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
        flash(e.message or _tr("تعذّر قراءة الجلسات المتصلة."), "error")
        return redirect(url_for("radius.subscriber_groups_list"))

    if disconnected:
        flash(_tr('تم إرسال أمر فصل %(disconnected)s جلسة من مجموعة «%(name)s».', disconnected=disconnected, name=group['name']), "success")
    elif failed:
        flash(_tr("تعذّر فصل جلسات المجموعة المتصلة."), "error")
    else:
        flash(_tr('لا توجد جلسات متصلة حالياً لمجموعة «%(name)s».', name=group['name']), "info")
    return redirect(url_for("radius.subscriber_groups_list"))


def sg_quota_reset_daily(gid: int):
    group = subscriber_groups_repo.get(_tid(), gid)
    if not group:
        abort(404)
    usernames = subscriber_groups_repo.list_member_usernames(_tid(), gid)
    if not usernames:
        flash(_tr("لا يوجد أعضاء في هذه المجموعة."), "info")
        return redirect(url_for("radius.subscriber_groups_list"))

    reset_count = 0
    failed = 0
    skipped = 0
    svc = get_users_service()
    from ..services.users import NothingToReset
    for username in usernames:
        try:
            svc.reset_daily_quota(actor=_actor(), username=username)
            reset_count += 1
        except NothingToReset:
            skipped += 1
        except RadiusError:
            failed += 1

    if reset_count:
        flash(_tr('تمت استعادة الكوتة اليومية لـ %(reset_count)s مشترك في مجموعة «%(name)s».', reset_count=reset_count, name=group['name']), "success")
    if skipped:
        flash(_tr('تُخطّي %(skipped)s مشترك بلا كوتة يوميّة ولا حدّ وقتٍ يوميّ.', skipped=skipped), "info")
    if failed:
        flash(_tr('تعذّرت استعادة الكوتة لـ %(failed)s مشترك.', failed=failed), "error")
    return redirect(url_for("radius.subscriber_groups_list"))
