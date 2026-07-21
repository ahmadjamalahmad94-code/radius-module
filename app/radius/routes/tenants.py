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
    # MT33 — حذف شبكة نهائيًّا: صفحة تأكيد (GET) ثم التنفيذ (POST).
    bp.add_url_rule("/tenants/<int:tenant_id>/delete", "tenants_delete_confirm",
                    tenants_delete_confirm, methods=["GET"])
    bp.add_url_rule("/tenants/<int:tenant_id>/delete", "tenants_delete",
                    tenants_delete, methods=["POST"])
    # MT18 — لوحة إدارة الاستضافة (هبوط المزوّد/المالك).
    bp.add_url_rule("/provider", "provider_home", provider_home, methods=["GET"])
    # MT36 — إغلاق طلب اشتراك وارد من صفحة هبوط المنصّة.
    bp.add_url_rule("/signup-requests/<int:request_id>/dismiss",
                    "signup_request_dismiss", signup_request_dismiss,
                    methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tenant_usage() -> tuple[dict, dict, dict, dict]:
    """MT18 — استهلاك كل جهة (مشتركون/NAS/متصلون + مشغّل) باستعلامات مجمّعة."""
    usage_subs: dict = {}
    usage_nas: dict = {}
    usage_online: dict = {}
    operators: dict = {}
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
        # أوّل مدير (غير سوبر) لكل جهة — للعرض في لوحة المزوّد.
        for r in db().execute(
                "SELECT m.tenant_id AS tid, a.username AS u FROM tenant_memberships m "
                "JOIN admins a ON a.id = m.admin_id "
                "WHERE COALESCE(a.is_super_admin,0)=0 AND a.enabled=1 "
                "GROUP BY m.tenant_id"):
            operators.setdefault(int(r["tid"]), r["u"])
    except Exception:  # noqa: BLE001 — عدّادات عرضية، لا تكسر الصفحة
        pass
    return usage_subs, usage_nas, usage_online, operators


def tenants_list():
    from datetime import datetime
    items = get_tenants_service().list()
    usage_subs, usage_nas, usage_online, _ = _tenant_usage()
    return render_template("radius/tenants_list.html", items=items,
                           tier_limits=TIER_LIMITS, now_utc=datetime.utcnow(),
                           usage_subs=usage_subs, usage_nas=usage_nas,
                           usage_online=usage_online)


def provider_home():
    """MT18 — لوحة إدارة الاستضافة: كل الجهات + فوترتها + صحة النظام + النسخ.
    owner-only (محروسة بـ_PERM_SUPER في blueprint)."""
    from datetime import datetime
    from ..core.tenant import (TENANT_STATUS_ACTIVE, TENANT_STATUS_TRIAL,
                               TENANT_STATUS_SUSPENDED, TENANT_STATUS_CLOSED)
    items = [t for t in get_tenants_service().list() if t.id != 1]  # نستثني مساحة المزوّد
    usage_subs, usage_nas, usage_online, operators = _tenant_usage()
    now = datetime.utcnow()

    # ملخّص KPI
    kpis = {
        "total":     len(items),
        "active":    sum(1 for t in items if t.status == TENANT_STATUS_ACTIVE),
        "trial":     sum(1 for t in items if t.status == TENANT_STATUS_TRIAL),
        "suspended": sum(1 for t in items if t.status == TENANT_STATUS_SUSPENDED),
        "paid":      sum(1 for t in items if (t.billing_mode or "free") == "paid"),
        "subs_total":   sum(usage_subs.get(t.id, 0) for t in items),
        "online_total": sum(usage_online.get(t.id, 0) for t in items),
    }

    # صحة النظام (owner-only)
    try:
        from ..services.dashboard_metrics import get_system_health
        system = get_system_health()
    except Exception:  # noqa: BLE001
        system = {}

    # النسخ الاحتياطي المحلّي
    try:
        from ..services.operations import get_operations_service
        ops = get_operations_service()
        backups = ops.list_local_backups(tenant_id=1)[:5]
        backup_count = len(ops.list_local_backups(tenant_id=1))
    except Exception:  # noqa: BLE001
        backups, backup_count = [], 0

    # MT36 — طلبات الاشتراك الواردة من صفحة هبوط المنصّة. المُعلَّقة فقط
    # تُعرَض هنا؛ المُعالَجة تبقى في القاعدة للأثر. فشل القراءة لا يُسقط
    # اللوحة (قد لا يكون الترحيل 167 طُبّق بعد على نسخةٍ قديمة).
    try:
        from ..db.repos import signup_requests_repo
        signups = signup_requests_repo.list_all(status="pending", limit=50)
    except Exception:  # noqa: BLE001
        signups = []

    return render_template(
        "radius/provider_home.html",
        items=items, now_utc=now, tier_limits=TIER_LIMITS,
        usage_subs=usage_subs, usage_nas=usage_nas, usage_online=usage_online,
        operators=operators, kpis=kpis, system=system,
        backups=backups, backup_count=backup_count, signups=signups)


def signup_request_dismiss(request_id: int):
    """MT36 — إغلاق طلب اشتراك (رفض/تمّت معالجته يدويًّا).

    القبول الفعليّ = إنشاء شبكة من «شبكة جديدة»؛ هذا المسار للإغلاق فقط
    كي لا تتراكم الطلبات المُنجَزة في اللوحة. لا يَحذف السجلّ — يُبقيه
    للأثر بحالة ``rejected``.
    """
    from ..db.repos import signup_requests_repo
    row = signup_requests_repo.get(request_id)
    if not row:
        abort(404)
    signup_requests_repo.mark(request_id, status="rejected", by=_actor())
    flash(f"أُغلق طلب «{row.get('network_name') or '—'}».", "info")
    return redirect(url_for("radius.provider_home"))


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
        # MT18 — الفوترة
        billing_mode=(request.form.get("billing_mode") or "free").strip(),
        billing_amount=_parse_amount(request.form.get("billing_amount")),
        paid_until=_parse_date(request.form.get("paid_until")),
        billing_note=(request.form.get("billing_note") or "").strip(),
    )


def _parse_amount(v) -> float:
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


def _parse_date(v):
    from datetime import datetime
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _form_changes() -> dict:
    keys = ("name", "display_name", "email", "phone", "currency", "locale",
            "timezone", "logo_url", "primary_color", "status", "plan_tier",
            "billing_mode", "billing_note")
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
    if request.form.get("billing_amount") is not None:
        out["billing_amount"] = _parse_amount(request.form.get("billing_amount"))
    if "paid_until" in request.form:
        out["paid_until"] = _parse_date(request.form.get("paid_until"))
    return out


def tenants_delete_confirm(tenant_id: int):
    """MT33 — صفحة «هل أنت متأكد؟»: تعرض ما سيُحذف بالتفصيل قبل التأكيد."""
    from ..services import tenant_delete
    try:
        info = tenant_delete.preview_tenant_deletion(tenant_id)
    except tenant_delete.TenantDeleteError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.tenants_list"))
    return render_template("radius/tenant_delete_confirm.html", **info)


def tenants_delete(tenant_id: int):
    """MT33 — التنفيذ. يُشترط كتابة الـslug حرفيًّا، وتُؤخذ نسخة أمان أوّلًا."""
    from ..services import tenant_delete
    try:
        out = tenant_delete.delete_tenant(
            tenant_id, confirm_slug=request.form.get("confirm_slug", ""),
            actor=_actor())
    except tenant_delete.TenantDeleteError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.tenants_delete_confirm", tenant_id=tenant_id))
    flash(f"حُذفت الشبكة «{out['name']}» نهائيًّا ({out['rows']} صفًّا، "
          f"{out['admins_deleted']} مديرًا). نسخة الأمان محفوظة: {out['safety_backup']}",
          "success")
    return redirect(url_for("radius.tenants_list"))
