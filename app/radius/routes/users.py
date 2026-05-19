"""Users (subscribers) routes — CRUD + extras.

RM-H1: extended with full AdvRadius fields.
Hybrid storage:
  - الحقول الـ queryable كأعمدة DB حقيقية (subscribers.* — انظر migration 011)
  - الحقول المتقدمة (MikroTik attrs, vendor-specific) في metadata JSON مُجمَّع
    {mikrotik:{}, radius:{}, advanced:{}, notifications:{}}
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import ACCOUNT_STATUSES, USER_TYPES
from ..core.errors import RadiusError
from ..core.types import Subscriber
from ..services.plans import get_plans_service
from ..services.users import get_users_service


# ════════════════════════════════════════════════════════════════
# RM-H1: metadata structure (نفس بنية HobeHub لتسهيل المقارنة)
# ════════════════════════════════════════════════════════════════
_META_GROUPS = {
    "mikrotik": [
        "mikrotik_filter_chain",
        "mikrotik_address_list",
        "mikrotik_framed_route",
        "mikrotik_user_group",
        "mikrotik_winbox_group",
        "mikrotik_queue_priority",
    ],
    "radius": [
        "framed_pool",
        "ppp_attributes_extra",
        "acct_interim_interval_sec",
    ],
    "advanced": [
        "temporary_speed_from",
        "temporary_speed_to",
    ],
    "notifications": [
        # placeholders للمرحلة القادمة
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


def _parse_metadata(raw: str | dict | None) -> dict:
    """يحوّل metadata من DB (str JSON) إلى dict مُجمَّع. fallback آمن."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}") or {}
        except (ValueError, TypeError):
            data = {}
    for g in _META_GROUPS:
        data.setdefault(g, {})
    return data


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
    """يجمع كل حقول الـ Subscriber form (الأساسية + RM-H1 الموسَّعة + metadata)."""
    def _i(n, d=0):
        try: return int(request.form.get(n) or d)
        except (TypeError, ValueError): return d
    def _b(n):
        return request.form.get(n, "") in ("1", "on", "true", "yes")
    def _s(n):
        return (request.form.get(n) or "").strip()

    plan_id = request.form.get("plan_id")
    manager_id = request.form.get("manager_id")

    # metadata: نجمع الحقول المسطّحة من الـ form ثم نُجمّعها
    flat_meta = {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if v:
            flat_meta[mf] = v
    meta_json = json.dumps(_flat_to_grouped(flat_meta), ensure_ascii=False)

    return Subscriber(
        id=sub_id,
        # حساب الإنترنت أساسي
        username=_s("username"),
        password=_s("password"),
        user_type=_s("user_type") or "subscriber",
        service_type=_s("service_type") or "Hotspot",
        plan_id=int(plan_id) if plan_id else None,
        manager_id=int(manager_id) if manager_id else None,
        group=_s("group"),
        pool=_s("pool"),
        status=_s("status") or "enabled",
        auto_renewal=_b("auto_renewal"),
        # PPPoE
        pppoe_username=_s("pppoe_username"),
        pppoe_password=_s("pppoe_password"),
        pppoe_ip=_s("pppoe_ip"),
        # شخصي
        full_name=_s("full_name"),
        father_name=_s("father_name"),
        mobile=_s("mobile"),
        email=_s("email"),
        national_id=_s("national_id"),
        nationality=_s("nationality"),
        country=_s("country"),
        city=_s("city"),
        district=_s("district"),
        state=_s("state"),
        zip=_s("zip"),
        address=_s("address"),
        payment_method=_s("payment_method"),
        payment_reference=_s("payment_reference"),
        # شبكة
        mac_lock=_s("mac_lock") or None,
        static_ip=_s("static_ip") or None,
        vlan_id=_i("vlan_id"),
        override_concurrent=_i("override_concurrent"),
        caller_id=_s("caller_id"),
        primary_dns_ppp=_s("primary_dns_ppp"),
        secondary_dns_ppp=_s("secondary_dns_ppp"),
        device_connection_file=_s("device_connection_file"),
        # سرعة (override per-user)
        bandwidth_control_enabled=_b("bandwidth_control_enabled"),
        download_speed_kbps=_i("download_speed_kbps"),
        upload_speed_kbps=_i("upload_speed_kbps"),
        custom_speed=_b("custom_speed"),
        temporary_speed=_b("temporary_speed"),
        # كوتا/وقت (override)
        total_connection_time_min=_i("total_connection_time_min"),
        daily_connection_time_min=_i("daily_connection_time_min"),
        download_quota_mb=_i("download_quota_mb"),
        upload_quota_mb=_i("upload_quota_mb"),
        combined_quota_mb=_i("combined_quota_mb"),
        connection_time_limit_enabled=_b("connection_time_limit_enabled"),
        quota_limit_enabled=_b("quota_limit_enabled"),
        equal_share_download=_b("equal_share_download"),
        equal_share_upload=_b("equal_share_upload"),
        # أيام + أجهزة + MACs
        working_days=_s("working_days"),
        device_count=_i("device_count", 1) or 1,
        allowed_macs=_s("allowed_macs"),
        # metadata JSON
        metadata=meta_json,
        # ربط
        beneficiary_ref=_s("beneficiary_ref"),
        remark=_s("remark"),
    )


def _sub_with_meta_for_template(sub: Subscriber) -> dict:
    """يحوّل sub إلى dict + يسطّح metadata للوصول البسيط من القالب."""
    from dataclasses import asdict
    d = asdict(sub)
    grouped = _parse_metadata(sub.metadata)
    flat = _grouped_to_flat(grouped)
    for f in _META_FIELDS:
        d.setdefault(f, flat.get(f, ""))
    return d


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
    empty = Subscriber(id=None, username="", password="", status="enabled")
    return render_template("radius/users_form.html",
        sub=_sub_with_meta_for_template(empty),
        plans=plans, statuses=ACCOUNT_STATUSES, user_types=USER_TYPES, is_new=True)


def users_create():
    dto = _form_dto()
    try:
        saved = get_users_service().create(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
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
        sub=_sub_with_meta_for_template(sub),
        plans=plans, statuses=ACCOUNT_STATUSES,
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
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
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
