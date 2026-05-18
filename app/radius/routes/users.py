"""Users (subscribers) routes — CRUD + extras."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import ACCOUNT_STATUSES, USER_TYPES
from ..core.errors import RadiusError
from ..core.types import Subscriber
from ..services.plans import get_plans_service
from ..services.users import get_users_service


def register_users_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/users", "users_list", users_list, methods=["GET"])
    bp.add_url_rule("/users/new", "users_new", users_new, methods=["GET"])
    bp.add_url_rule("/users", "users_create", users_create, methods=["POST"])
    bp.add_url_rule("/users/<username>/edit", "users_edit", users_edit, methods=["GET"])
    bp.add_url_rule("/users/<username>", "users_update", users_update, methods=["POST"])
    bp.add_url_rule("/users/<username>/delete", "users_delete", users_delete, methods=["POST"])
    bp.add_url_rule("/users/<username>/toggle", "users_toggle", users_toggle, methods=["POST"])
    bp.add_url_rule("/users/<username>/extend", "users_extend", users_extend, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _form_dto(*, sub_id: int | None = None) -> Subscriber:
    def _i(n, d=0):
        try: return int(request.form.get(n) or d)
        except: return d
    plan_id = request.form.get("plan_id")
    return Subscriber(
        id=sub_id,
        username=(request.form.get("username") or "").strip(),
        password=(request.form.get("password") or "").strip(),
        user_type=(request.form.get("user_type") or "subscriber"),
        plan_id=int(plan_id) if plan_id else None,
        full_name=(request.form.get("full_name") or "").strip(),
        mobile=(request.form.get("mobile") or "").strip(),
        email=(request.form.get("email") or "").strip(),
        address=(request.form.get("address") or "").strip(),
        national_id=(request.form.get("national_id") or "").strip(),
        status=(request.form.get("status") or "enabled"),
        mac_lock=(request.form.get("mac_lock") or "").strip() or None,
        static_ip=(request.form.get("static_ip") or "").strip() or None,
        vlan_id=_i("vlan_id"),
        override_concurrent=_i("override_concurrent"),
        remark=(request.form.get("remark") or "").strip(),
        beneficiary_ref=(request.form.get("beneficiary_ref") or "").strip(),
    )


# ─────────────── views ───────────────

def users_list():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip() or None
    plan_id = request.args.get("plan_id")
    plan_id = int(plan_id) if plan_id else None
    items = get_users_service().list(status=status, plan_id=plan_id, search=q, limit=1000)
    plans = list(get_plans_service().list(limit=500))
    return render_template("radius/users_list.html",
        items=items, plans=plans, q=q, status=status, plan_id=plan_id,
        statuses=ACCOUNT_STATUSES)


def users_new():
    plans = list(get_plans_service().list(limit=500))
    return render_template("radius/users_form.html",
        sub=Subscriber(id=None, username="", password="", status="enabled"),
        plans=plans, statuses=ACCOUNT_STATUSES, user_types=USER_TYPES, is_new=True)


def users_create():
    dto = _form_dto()
    try:
        saved = get_users_service().create(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=dto, plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=True), 400
    flash(f"تم إنشاء المستخدم «{saved.username}».", "success")
    return redirect(url_for("radius.users_list"))


def users_edit(username: str):
    try:
        sub = get_users_service().get(username)
    except RadiusError:
        abort(404)
    plans = list(get_plans_service().list(limit=500))
    return render_template("radius/users_form.html",
        sub=sub, plans=plans, statuses=ACCOUNT_STATUSES,
        user_types=USER_TYPES, is_new=False)


def users_update(username: str):
    dto = _form_dto()
    # احرص أن الـ username لا يتغير عن المسار
    from dataclasses import replace
    dto = replace(dto, username=username)
    try:
        get_users_service().update(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=dto, plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=False), 400
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.users_list"))


def users_delete(username: str):
    try:
        get_users_service().delete(actor=_actor(), username=username)
        flash("تم الحذف.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_toggle(username: str):
    try:
        u = get_users_service().get(username)
        if u.status == "enabled":
            get_users_service().disable(actor=_actor(), username=username)
            flash("تم التعطيل.", "warning")
        else:
            get_users_service().enable(actor=_actor(), username=username)
            flash("تم التفعيل.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_extend(username: str):
    minutes = request.form.get("minutes")
    try:
        m = int(minutes)
        get_users_service().extend_time(actor=_actor(), username=username, minutes=m)
        flash(f"تم تمديد الحساب {m} دقيقة.", "success")
    except (TypeError, ValueError):
        flash("قيمة دقائق غير صحيحة", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))
