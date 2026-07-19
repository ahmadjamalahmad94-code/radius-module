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
    # MT8 — أفعال سريعة لإدارة التجارب من القائمة.
    bp.add_url_rule("/tenants/<int:tenant_id>/trial-extend", "tenants_trial_extend",
                    tenants_trial_extend, methods=["POST"])
    bp.add_url_rule("/tenants/<int:tenant_id>/toggle-suspend", "tenants_toggle_suspend",
                    tenants_toggle_suspend, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def tenants_list():
    from datetime import datetime
    items = get_tenants_service().list()
    # MT14 — استهلاك كل جهة بثلاثة استعلامات مجمّعة (لا استعلام لكل صف).
    usage_subs: dict = {}
    usage_nas: dict = {}
    usage_online: dict = {}
    try:
        from ..db.connection import db
        for r in db().execute(
                "SELECT tenant_id, COUNT(*) AS n FROM subscribers "
                "WHERE deleted_at IS NULL AND COALESCE(user_type,'') != 'card' "
                "GROUP BY tenant_id"):
            usage_subs[int(r["tenant_id"])] = int(r["n"])
        for r in db().execute(
                "SELECT tenant_id, COUNT(*) AS n FROM nas_devices "
                "WHERE deleted_at IS NULL GROUP BY tenant_id"):
            usage_nas[int(r["tenant_id"])] = int(r["n"])
        for r in db().execute(
                "SELECT tenant_id, COUNT(*) AS n FROM radacct "
                "WHERE acctstoptime IS NULL AND COALESCE(username,'') != '' "
                "GROUP BY tenant_id"):
            usage_online[int(r["tenant_id"])] = int(r["n"])
    except Exception:  # noqa: BLE001 — عدّادات عرضية، لا تكسر الصفحة
        pass
    return render_template("radius/tenants_list.html", items=items,
                           tier_limits=TIER_LIMITS, now_utc=datetime.utcnow(),
                           usage_subs=usage_subs, usage_nas=usage_nas,
                           usage_online=usage_online)


def tenants_trial_extend(tenant_id: int):
    """MT8 — تمديد التجربة: من الأبعد بين الآن ونهايتها الحالية + N أيام."""
    from datetime import datetime, timedelta
    svc = get_tenants_service()
    t = svc.get(tenant_id)
    if not t:
        abort(404)
    try:
        days = max(1, min(90, int(request.form.get("days") or 7)))
    except ValueError:
        days = 7
    base = t.trial_ends_at if (t.trial_ends_at and t.trial_ends_at > datetime.utcnow()) \
        else datetime.utcnow()
    new_end = base + timedelta(days=days)
    svc.update(actor=_actor(), tenant_id=tenant_id,
               status=TENANT_STATUS_TRIAL, trial_ends_at=new_end)
    flash(f"مُدّدت تجربة «{t.display_name or t.name}» حتى {new_end.strftime('%Y-%m-%d')}.",
          "success")
    return redirect(url_for("radius.tenants_list"))


def tenants_toggle_suspend(tenant_id: int):
    """MT8 — تعليق/إعادة تفعيل سريع. إعادة التفعيل تعيدها «تجريبية» إذا
    كانت مدة تجربتها ما تزال سارية، وإلا «مفعَّلة»."""
    from datetime import datetime
    svc = get_tenants_service()
    t = svc.get(tenant_id)
    if not t:
        abort(404)
    if t.status == TENANT_STATUS_SUSPENDED:
        new_status = (TENANT_STATUS_TRIAL
                      if (t.trial_ends_at and t.trial_ends_at > datetime.utcnow())
                      else TENANT_STATUS_ACTIVE)
        msg = "أُعيد تفعيل الجهة"
    else:
        new_status = TENANT_STATUS_SUSPENDED
        msg = "عُلِّقت الجهة — يُرفض اتصال مشتركيها فورًا"
    svc.update(actor=_actor(), tenant_id=tenant_id, status=new_status)
    flash(f"{msg}: «{t.display_name or t.name}».", "success")
    return redirect(url_for("radius.tenants_list"))


def tenants_new():
    blank = Tenant(id=None, slug="", name="", display_name="",
                   plan_tier=TENANT_TIER_STARTER, status=TENANT_STATUS_ACTIVE)
    return render_template("radius/tenants_form.html",
        tenant=blank, tiers=TIER_KEYS, statuses=STATUS_KEYS,
        tier_limits=TIER_LIMITS, is_new=True)


def tenants_create():
    t = _form_dto()
    # MT6 — بذر مدير الجهة (غير سوبر) اختياريًا في نفس الخطوة؛ إلزامي
    # عمليًا للجهات التجريبية كي يتسلّم الزبون بيانات دخول جاهزة.
    op_user = (request.form.get("operator_username") or "").strip()
    try:
        if op_user:
            result = get_tenants_service().create_trial(
                actor=_actor(), tenant=t,
                trial_days=int(request.form.get("trial_days") or 7),
                operator_username=op_user,
                operator_password=request.form.get("operator_password") or "",
                operator_full_name=(request.form.get("operator_full_name") or "").strip(),
            )
            saved = result["tenant"]
            ends = result["trial_ends_at"]
            parts = [f"تم إنشاء الجهة «{saved.display_name or saved.name}»"]
            if ends:
                parts.append(f"(التجربة حتى {ends.strftime('%Y-%m-%d')})")
            parts.append(f"— دخول المدير: {result['operator_username']} / "
                         f"{result['operator_password']} (احفظها الآن، لن تظهر مجددًا)")
            parts.append(f"— بوابة المشتركين: /portal/subscriber/login?t={saved.slug}")
            flash(" ".join(parts), "success")
        else:
            if (t.status or "") == TENANT_STATUS_TRIAL:
                raise RadiusError("الجهة التجريبية تحتاج اسم مستخدم لمديرها — "
                                   "املأ حقل «مدير الجهة».")
            saved = get_tenants_service().create(actor=_actor(), tenant=t)
            flash(f"تم إنشاء الجهة «{saved.name}».", "success")
    except (RadiusError, ValueError) as e:
        flash(str(getattr(e, "message", e)), "error")
        return render_template("radius/tenants_form.html",
            tenant=t, tiers=TIER_KEYS, statuses=STATUS_KEYS,
            tier_limits=TIER_LIMITS, is_new=True), 400
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
