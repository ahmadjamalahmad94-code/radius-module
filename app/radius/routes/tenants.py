"""routes إدارة الـ tenants — للأدمن super_admin بشكل أساسي."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..core.system_config import default_currency
from ..core.tenant import (
    TIER_LIMITS, Tenant,
    TENANT_STATUS_ACTIVE, TENANT_STATUS_SUSPENDED, TENANT_STATUS_TRIAL, TENANT_STATUS_CLOSED,
    TENANT_TIER_STARTER, TENANT_TIER_PRO, TENANT_TIER_ENTERPRISE,
)
from ..services.tenants import get_tenants_service


TIER_KEYS = (TENANT_TIER_STARTER, TENANT_TIER_PRO, TENANT_TIER_ENTERPRISE)
STATUS_KEYS = (TENANT_STATUS_ACTIVE, TENANT_STATUS_TRIAL, TENANT_STATUS_SUSPENDED, TENANT_STATUS_CLOSED)


def register_tenants_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/tenants", "tenants_list", tenants_list, methods=["GET"])
    bp.add_url_rule("/tenants/new", "tenants_new", tenants_new, methods=["GET"])
    bp.add_url_rule("/tenants", "tenants_create", tenants_create, methods=["POST"])
    bp.add_url_rule("/tenants/<int:tenant_id>/edit", "tenants_edit", tenants_edit, methods=["GET"])
    bp.add_url_rule("/tenants/<int:tenant_id>", "tenants_update", tenants_update, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def tenants_list():
    items = get_tenants_service().list()
    return render_template("radius/tenants_list.html", items=items, tier_limits=TIER_LIMITS)


def tenants_new():
    blank = Tenant(id=None, slug="", name="", display_name="",
                   plan_tier=TENANT_TIER_STARTER, status=TENANT_STATUS_ACTIVE)
    return render_template("radius/tenants_form.html",
        tenant=blank, tiers=TIER_KEYS, statuses=STATUS_KEYS,
        tier_limits=TIER_LIMITS, is_new=True)


def tenants_create():
    t = _form_dto()
    try:
        saved = get_tenants_service().create(actor=_actor(), tenant=t)
    except (RadiusError, ValueError) as e:
        flash(str(getattr(e, "message", e)), "error")
        return render_template("radius/tenants_form.html",
            tenant=t, tiers=TIER_KEYS, statuses=STATUS_KEYS,
            tier_limits=TIER_LIMITS, is_new=True), 400
    flash(f"تم إنشاء Tenant «{saved.name}».", "success")
    return redirect(url_for("radius.tenants_list"))


def tenants_edit(tenant_id: int):
    t = get_tenants_service().get(tenant_id)
    if not t:
        abort(404)
    return render_template("radius/tenants_form.html",
        tenant=t, tiers=TIER_KEYS, statuses=STATUS_KEYS,
        tier_limits=TIER_LIMITS, is_new=False)


def tenants_update(tenant_id: int):
    changes = _form_changes()
    try:
        get_tenants_service().update(actor=_actor(), tenant_id=tenant_id, **changes)
    except RadiusError as e:
        flash(e.message, "error")
        t = get_tenants_service().get(tenant_id)
        return render_template("radius/tenants_form.html",
            tenant=t, tiers=TIER_KEYS, statuses=STATUS_KEYS,
            tier_limits=TIER_LIMITS, is_new=False), 400
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.tenants_list"))


def _form_dto() -> Tenant:
    def _i(n, d=0):
        try: return int(request.form.get(n) or d)
        except: return d
    return Tenant(
        id=None,
        slug=(request.form.get("slug") or "").strip().lower(),
        name=(request.form.get("name") or "").strip(),
        display_name=(request.form.get("display_name") or "").strip(),
        email=(request.form.get("email") or "").strip(),
        phone=(request.form.get("phone") or "").strip(),
        currency=(request.form.get("currency") or default_currency()).strip(),
        locale=(request.form.get("locale") or "ar").strip(),
        timezone=(request.form.get("timezone") or "Asia/Amman").strip(),
        logo_url=(request.form.get("logo_url") or "").strip(),
        primary_color=(request.form.get("primary_color") or "#2BAACC").strip(),
        status=(request.form.get("status") or TENANT_STATUS_ACTIVE).strip(),
        plan_tier=(request.form.get("plan_tier") or TENANT_TIER_STARTER).strip(),
        max_subscribers=_i("max_subscribers"),
        max_nas=_i("max_nas"),
        api_rpm=_i("api_rpm"),
    )


def _form_changes() -> dict:
    keys = ("name", "display_name", "email", "phone", "currency", "locale",
            "timezone", "logo_url", "primary_color", "status", "plan_tier")
    out: dict = {}
    for k in keys:
        v = request.form.get(k)
        if v is not None:
            out[k] = v.strip()
    for k in ("max_subscribers", "max_nas", "api_rpm"):
        v = request.form.get(k)
        if v is not None:
            try: out[k] = int(v)
            except ValueError: pass
    return out
