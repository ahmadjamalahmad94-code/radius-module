"""Self-scoped subscriber and card user portal routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusValidationError
from ..db.repos import tenants_repo
from ..services.customer_portals import CustomerPortalService, PortalAuthError


def register_customer_portal_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/customer-portals", "customer_portals_admin", customer_portals_admin, methods=["GET"])
    bp.add_url_rule("/portal/subscriber/login", "portal_subscriber_login", subscriber_login, methods=["GET", "POST"])
    bp.add_url_rule("/portal/subscriber/logout", "portal_subscriber_logout", subscriber_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/subscriber", "portal_subscriber_home", subscriber_home, methods=["GET"])
    bp.add_url_rule("/portal/subscriber/loan-request", "portal_subscriber_loan_request", subscriber_loan_request, methods=["POST"])
    bp.add_url_rule("/portal/subscriber/renewal-request", "portal_subscriber_renewal_request", subscriber_renewal_request, methods=["POST"])
    bp.add_url_rule("/portal/card/login", "portal_card_login", card_login, methods=["GET", "POST"])
    bp.add_url_rule("/portal/card/logout", "portal_card_logout", card_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/card", "portal_card_home", card_home, methods=["GET"])
    bp.add_url_rule("/portal/card/purchase", "portal_card_purchase", card_purchase, methods=["POST"])
    bp.add_url_rule("/portal/card/redeem", "portal_card_redeem", card_redeem, methods=["POST"])


def build_customer_portal_root_blueprint() -> Blueprint:
    """Returns a blueprint that exposes the customer-facing portal
    pages at root URLs (e.g. /portal/card) — no admin/radius prefix.

    Customers shouldn't see internal admin paths. The same view
    functions are reused; only the URL space differs.
    """
    bp = Blueprint("portal", __name__)
    # Subscriber portal
    bp.add_url_rule("/portal/subscriber/login",  "subscriber_login",  subscriber_login,  methods=["GET", "POST"])
    bp.add_url_rule("/portal/subscriber/logout", "subscriber_logout", subscriber_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/subscriber",        "subscriber_home",   subscriber_home,   methods=["GET"])
    bp.add_url_rule("/portal/subscriber/loan-request",    "subscriber_loan_request",    subscriber_loan_request,    methods=["POST"])
    bp.add_url_rule("/portal/subscriber/renewal-request", "subscriber_renewal_request", subscriber_renewal_request, methods=["POST"])
    # Card user portal
    bp.add_url_rule("/portal/card/login",    "card_login",    card_login,    methods=["GET", "POST"])
    bp.add_url_rule("/portal/card/logout",   "card_logout",   card_logout,   methods=["GET", "POST"])
    bp.add_url_rule("/portal/card",          "card_home",     card_home,     methods=["GET"])
    bp.add_url_rule("/portal/card/purchase", "card_purchase", card_purchase, methods=["POST"])
    bp.add_url_rule("/portal/card/redeem",   "card_redeem",   card_redeem,   methods=["POST"])
    return bp


def _tenant_id() -> int:
    return int(session.get("portal_tenant_id") or 1)


def _svc() -> CustomerPortalService:
    return CustomerPortalService(tenant_id=_tenant_id())


# ── مفاتيح «صفحة المشترك» (portal.*) ──────────────────────────────
# تُحفظ من تبويب «صفحة المشترك» في صفحة الإعدادات، وتتحكّم بما يظهر
# للمشترك في بوابته وما يُسمح له به. تُقرأ هنا بنفس صيغة الإعدادات
# المنطقية ('1'/'true'/'on'…) المستخدمة في settings_page.html.
_PORTAL_TRUE = ("1", "true", "t", "on", "yes")


def _portal_flag(key: str, default: str = "1") -> bool:
    """يقرأ مفتاح portal.* كقيمة منطقية من إعدادات المستأجر."""
    raw = tenants_repo.get_setting(_tenant_id(), key, default)
    return str(raw or "").strip().lower() in _PORTAL_TRUE


def _portal_flags() -> dict:
    """كل أعلام البوابة لتمريرها للقالب.

    الستة الأولى مربوطة فعليًا (عرض/منع في البوابة). الثلاثة الأخيرة
    (تغيير كلمة المرور، الشراء الذاتي، تغيير الباقة) ميزات غير موجودة
    في البوابة بعد — تبقى «قيد الربط»: تُقرأ وتُمرَّر لكنها بلا أثر."""
    return {
        "show_usage":            _portal_flag("portal.show_usage"),
        "show_sessions":         _portal_flag("portal.show_sessions"),
        "show_invoices":         _portal_flag("portal.show_invoices"),
        "allow_renewal_request": _portal_flag("portal.allow_renewal_request"),
        "allow_loan_request":    _portal_flag("portal.allow_loan_request"),
        "show_support":          _portal_flag("portal.show_support"),
        # قيد الربط — مخزّنة وجاهزة، لا أثر لها في البوابة بعد:
        "allow_password_change": _portal_flag("portal.allow_password_change"),
        "allow_self_purchase":   _portal_flag("portal.allow_self_purchase", "0"),
        "allow_plan_change":     _portal_flag("portal.allow_plan_change", "0"),
    }


def _portal_denied(message: str):
    """رفض طلب POST لميزة مُعطّلة في الإعدادات — 403 برسالة عربية.
    يمنع تجاوز إخفاء النموذج عبر استدعاء المسار مباشرة بالـURL."""
    return message, 403


def customer_portals_admin():
    return render_template("radius/customer_portals_admin.html")


def subscriber_login():
    if request.method == "POST":
        try:
            _u = request.form.get("username") or ""
            subscriber = CustomerPortalService(tenant_id=1).authenticate_subscriber(
                username=_u,
                password=request.form.get("password") or "",
            )
        except PortalAuthError:
            from ..services.login_events import record_login_event
            record_login_event(actor_type="subscriber", username=request.form.get("username") or "",
                               success=False, reason="bad_password", tenant_id=1,
                               attempted_password=request.form.get("password") or "")
            flash("بيانات دخول المشترك غير صحيحة.", "error")
            return render_template("radius/portal_subscriber_login.html"), 401
        from ..services.login_events import record_login_event
        record_login_event(actor_type="subscriber", username=subscriber.get("username") or _u,
                           success=True, actor_id=subscriber.get("id"), tenant_id=1)
        session["portal_tenant_id"] = 1
        session["portal_subscriber_id"] = int(subscriber["id"])
        session.pop("portal_card_user_id", None)
        return redirect(url_for("portal.subscriber_home"))
    return render_template("radius/portal_subscriber_login.html")


def subscriber_logout():
    session.pop("portal_subscriber_id", None)
    flash("تم تسجيل الخروج من بوابة المشترك.", "info")
    return redirect(url_for("portal.subscriber_login"))


def subscriber_home():
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("portal.subscriber_login"))
    dashboard = _svc().subscriber_dashboard(int(subscriber_id))
    return render_template(
        "radius/portal_subscriber.html",
        dashboard=dashboard,
        portal_flags=_portal_flags(),
    )


def subscriber_loan_request():
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("portal.subscriber_login"))
    # حارس الإعداد: إن كان «طلب السلفة» مُعطّلًا في صفحة المشترك نرفض
    # الطلب بـ403 حتى لو استُدعي المسار مباشرة بالـURL (النموذج مخفي
    # أصلًا في البوابة).
    if not _portal_flag("portal.allow_loan_request"):
        return _portal_denied("طلب سلفة الوقت غير مُفعَّل في بوابة المشترك حاليًا.")
    try:
        result = _svc().submit_loan_request(
            subscriber_id=int(subscriber_id),
            requested_minutes=int(request.form.get("requested_minutes") or 0),
            reason=request.form.get("reason") or "",
        )
        flash(f"تم تسجيل طلب السلفة. الحالة: {_portal_status_label(result['status'])}", "success")
    except (RadiusValidationError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("portal.subscriber_home"))


def subscriber_renewal_request():
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("portal.subscriber_login"))
    reason = request.form.get("reason") or ""
    # هذا المسار يخدم نموذجين: «تجديد الاشتراك» و«الدعم/الشكاوى» (الأخير
    # يُرسَل عبر نفس القناة ببادئة [شكوى]). نحرس كلًّا بمفتاحه المناسب،
    # ونرفض بـ403 إن كان مُعطّلًا (يمنع التجاوز المباشر بالـURL).
    is_support = reason.lstrip().startswith("[شكوى]")
    if is_support:
        if not _portal_flag("portal.show_support"):
            return _portal_denied("قسم الدعم/الشكاوى غير مُفعَّل في بوابة المشترك حاليًا.")
    elif not _portal_flag("portal.allow_renewal_request"):
        return _portal_denied("طلب التجديد غير مُفعَّل في بوابة المشترك حاليًا.")
    result = _svc().submit_renewal_request(
        subscriber_id=int(subscriber_id),
        reason=reason,
    )
    flash(f"تم تسجيل الطلب. الحالة: {_portal_status_label(result['status'])}", "success")
    return redirect(url_for("portal.subscriber_home"))


def card_login():
    if request.method == "POST":
        try:
            _mob = request.form.get("mobile") or ""
            card_user = CustomerPortalService(tenant_id=1).authenticate_card_user(
                mobile=_mob,
                password=request.form.get("password") or "",
            )
        except PortalAuthError:
            from ..services.login_events import record_login_event
            record_login_event(actor_type="card", username=request.form.get("mobile") or "",
                               success=False, reason="bad_password", tenant_id=1,
                               attempted_password=request.form.get("password") or "")
            flash("رقم الجوال أو كلمة المرور غير صحيحة.", "error")
            return render_template("radius/portal_card_login.html"), 401
        from ..services.login_events import record_login_event
        record_login_event(actor_type="card", username=str(card_user.get("mobile") or _mob),
                           success=True, actor_id=card_user.get("id"), tenant_id=1)
        session["portal_tenant_id"] = 1
        session["portal_card_user_id"] = int(card_user["id"])
        session.pop("portal_subscriber_id", None)
        return redirect(url_for("portal.card_home"))
    return render_template("radius/portal_card_login.html")


def card_logout():
    session.pop("portal_card_user_id", None)
    flash("تم تسجيل الخروج من بوابة الكروت.", "info")
    return redirect(url_for("portal.card_login"))


def card_home():
    card_user_id = session.get("portal_card_user_id")
    if not card_user_id:
        return redirect(url_for("portal.card_login"))
    dashboard = _svc().card_user_dashboard(int(card_user_id))
    # MikroTik captive portals expose /login on the gateway. Most
    # default configurations DNS-rewrite "hotspot" to the gateway
    # IP so http://hotspot/login Just Works while connected, and
    # operators can override via env var when the IP differs.
    import os
    hotspot_login_url = (
        os.environ.get("HOBERADIUS_HOTSPOT_LOGIN_URL")
        or "http://hotspot/login"
    )
    return render_template(
        "radius/portal_card.html",
        dashboard=dashboard,
        hotspot_login_url=hotspot_login_url,
    )


def card_purchase():
    card_user_id = session.get("portal_card_user_id")
    if not card_user_id:
        return redirect(url_for("portal.card_login"))
    try:
        purchase = _svc().purchase_card_package(
            card_user_id=int(card_user_id),
            package_id=int(request.form.get("package_id") or 0),
        )
        flash(f"تم شراء الكرت رقم #{purchase['id']}.", "success")
    except (ValueError, RadiusValidationError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("portal.card_home"))


def _portal_status_label(status: str) -> str:
    return {
        "pending": "بانتظار المراجعة",
        "auto_approved": "معتمد تلقائيًا",
        "requires_approval": "يحتاج موافقة",
        "rejected": "مرفوض",
    }.get(str(status or ""), str(status or "غير معروف"))


def card_redeem():
    card_user_id = session.get("portal_card_user_id")
    if not card_user_id:
        return redirect(url_for("portal.card_login"))
    try:
        result = _svc().redeem_card_to_wallet(
            card_user_id=int(card_user_id),
            card_number=request.form.get("card_number") or "",
            card_password=request.form.get("card_password") or "",
        )
        flash(f"تم شحن الرصيد بقيمة {result['amount']:.2f}.", "success")
    except (ValueError, RadiusValidationError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("portal.card_home"))
