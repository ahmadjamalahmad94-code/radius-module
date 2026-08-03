"""Self-scoped subscriber and card user portal routes."""
from __future__ import annotations

from flask import (Blueprint, Response, flash, redirect, render_template,
                   request, session, url_for)

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
    bp.add_url_rule("/portal/subscriber/data-connection", "portal_subscriber_data_connection", subscriber_data_connection, methods=["POST"])
    bp.add_url_rule("/portal/subscriber/data-connection/download", "portal_subscriber_data_connection_download", subscriber_data_connection_download, methods=["GET"])
    bp.add_url_rule("/portal/subscriber/telegram/connect/start", "portal_subscriber_telegram_connect_start", subscriber_telegram_connect_start, methods=["POST"])
    bp.add_url_rule("/portal/subscriber/telegram/connect/poll", "portal_subscriber_telegram_connect_poll", subscriber_telegram_connect_poll, methods=["POST"])
    bp.add_url_rule("/portal/card/login", "portal_card_login", card_login, methods=["GET", "POST"])
    bp.add_url_rule("/portal/card/logout", "portal_card_logout", card_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/card", "portal_card_home", card_home, methods=["GET"])
    bp.add_url_rule("/portal/card/purchase", "portal_card_purchase", card_purchase, methods=["POST"])
    bp.add_url_rule("/portal/card/redeem", "portal_card_redeem", card_redeem, methods=["POST"])
    bp.add_url_rule("/portal/distributor/login", "portal_distributor_login", distributor_login, methods=["GET", "POST"])
    bp.add_url_rule("/portal/distributor/logout", "portal_distributor_logout", distributor_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/distributor", "portal_distributor_home", distributor_home, methods=["GET"])


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
    bp.add_url_rule("/portal/subscriber/data-connection", "subscriber_data_connection", subscriber_data_connection, methods=["POST"])
    bp.add_url_rule("/portal/subscriber/data-connection/download", "subscriber_data_connection_download", subscriber_data_connection_download, methods=["GET"])
    bp.add_url_rule("/portal/subscriber/telegram/connect/start", "subscriber_telegram_connect_start", subscriber_telegram_connect_start, methods=["POST"])
    bp.add_url_rule("/portal/subscriber/telegram/connect/poll",  "subscriber_telegram_connect_poll",  subscriber_telegram_connect_poll,  methods=["POST"])
    # Card user portal
    bp.add_url_rule("/portal/card/login",    "card_login",    card_login,    methods=["GET", "POST"])
    bp.add_url_rule("/portal/card/logout",   "card_logout",   card_logout,   methods=["GET", "POST"])
    bp.add_url_rule("/portal/card",          "card_home",     card_home,     methods=["GET"])
    bp.add_url_rule("/portal/card/purchase", "card_purchase", card_purchase, methods=["POST"])
    bp.add_url_rule("/portal/card/redeem",   "card_redeem",   card_redeem,   methods=["POST"])
    # بوابة الموزّع — فحص كروت للقراءة فقط (صلاحية cards.check)
    bp.add_url_rule("/portal/distributor/login",  "distributor_login",  distributor_login,  methods=["GET", "POST"])
    bp.add_url_rule("/portal/distributor/logout", "distributor_logout", distributor_logout, methods=["GET", "POST"])
    bp.add_url_rule("/portal/distributor",        "distributor_home",   distributor_home,   methods=["GET"])
    # «انتهى اشتراكك» captive/renew page — PUBLIC (no login), HTTP-reachable by
    # blocked subscribers (it's in the router walled-garden allow-list).
    bp.add_url_rule("/p/expired", "subscription_expired", subscription_expired, methods=["GET"])
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
        # feat/data-connection-oneclick — زر «اتصال بيانات». مفعّل افتراضيًا؛
        # دورمنت عمليًّا حتى يُضبط النطاق الفرعي/مفتاح WG في الإعدادات.
        "allow_data_connection": _portal_flag("portal.allow_data_connection"),
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
                               success=False, reason="bad_password", tenant_id=1)
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
    # feat/data-connection-oneclick — السياق لتبويب «اتصال بيانات». نقرأ
    # ناتج آخر توليد من الجلسة (إن وُجد) لعرض السكربت بعد الضغط.
    last = session.get("dc_last")
    return render_template(
        "radius/portal_subscriber.html",
        dashboard=dashboard,
        portal_flags=_portal_flags(),
        data_connection=_data_connection_context(),
        dc_result=last if isinstance(last, dict) else None,
        tg_connect=_subscriber_tg_status(int(subscriber_id)),
    )


# ── feat/telegram-one-click-connect — ربط تيليجرام للمشترك ───────────────
# يُعيد استخدام آليّة الربط الموحّدة (telegram_connect) بنطاق «subscriber»: رمز
# لكل مشترك، والتقاط chat_id يُخزَّن على ملف المشترك. البوت هو بوت المستأجر
# نفسه الذي أنشأه المدير مرّة واحدة — فالمشترك يربط بضغطة دون أي توكن.
def _subscriber_tg_status(subscriber_id: int) -> dict:
    """حالة ربط تيليجرام للمشترك (هل يوجد بوت للمستأجر؟ مربوط؟ + الاسم)."""
    try:
        from ..services import telegram_connect
        return telegram_connect.connection_status(
            _tenant_id(), scope="subscriber", subscriber_id=int(subscriber_id))
    except Exception:  # noqa: BLE001 — لا يكسر صفحة المشترك أبدًا
        return {"has_token": False, "linked": False,
                "account_name": "", "chat_id_masked": ""}


def subscriber_telegram_connect_start():
    """يبدأ نافذة ربط تيليجرام للمشترك المسجَّل. JSON (لا 500)."""
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return {"ok": False, "error": "الجلسة منتهية."}, 401
    from flask import jsonify
    from ..services import telegram_connect
    res = telegram_connect.start_link(
        _tenant_id(), scope="subscriber", subscriber_id=int(subscriber_id))
    return jsonify(res)


def subscriber_telegram_connect_poll():
    """استطلاع التقاط chat_id للمشترك — يُستدعى كل ~2ث أثناء النافذة. JSON."""
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return {"ok": False, "error": "الجلسة منتهية."}, 401
    from flask import jsonify
    from ..services import telegram_connect
    res = telegram_connect.poll_link(
        _tenant_id(), scope="subscriber", subscriber_id=int(subscriber_id))
    return jsonify(res)


def _data_connection_context() -> dict:
    """جاهزية ميزة «اتصال بيانات» للعرض في القالب (دون كشف أي سرّ)."""
    from ..services import data_connection as dc
    return {
        "configured": bool(dc.client_subdomain()),
        "speed_kbit": dc.DATA_SPEED_KBIT,
    }


def subscriber_data_connection():
    """ينشئ الحساب على الـVPS ويبني سكربت الاتصال (زر «اتصال بيانات»)."""
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("portal.subscriber_login"))
    if not _portal_flag("portal.allow_data_connection"):
        return _portal_denied("ميزة «اتصال بيانات» غير مُفعَّلة في بوابة المشترك حاليًا.")
    from ..services import data_connection as dc
    from ..services import data_connection_provision as dcp
    try:
        result = dcp.provision_data_connection(
            tenant_id=_tenant_id(),
            subscriber_id=int(subscriber_id),
            version=request.form.get("version") or "",
            protocol=request.form.get("protocol") or "",
        )
        session["dc_last"] = {
            "version": result.version,
            "protocol": result.protocol,
            "filename": result.filename,
            "script": result.script,
            "target_host": result.target_host,
            "speed_kbit": result.speed_kbit,
        }
        flash("تم إنشاء الاتصال. انسخ السكربت أو نزّله والصقه في المايكروتيك.", "success")
    except dc.DataConnectionError as exc:
        session.pop("dc_last", None)
        flash(str(exc), "error")
    return redirect(url_for("portal.subscriber_home") + "#pane-data")


def subscriber_data_connection_download():
    """ينزّل آخر سكربت تمّ توليده (من الجلسة) كملفّ .rsc."""
    subscriber_id = session.get("portal_subscriber_id")
    if not subscriber_id:
        return redirect(url_for("portal.subscriber_login"))
    last = session.get("dc_last")
    if not isinstance(last, dict) or not last.get("script"):
        flash("لا يوجد سكربت لتنزيله. أنشئ الاتصال أولًا.", "error")
        return redirect(url_for("portal.subscriber_home") + "#pane-data")
    filename = str(last.get("filename") or "hobe-data.rsc")
    return Response(
        last["script"] + "\n",
        mimetype="application/x-rsc",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
                               success=False, reason="bad_password", tenant_id=1)
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
    from ..core import env_settings
    hotspot_login_url = (
        env_settings.env("HOBERADIUS_HOTSPOT_LOGIN_URL")
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


# ── بوابة الموزّع — «فحص كروت» للقراءة فقط ─────────────────────────────
# الموزّع سجلّ محاسبي بلا حساب مدير؛ دخوله هنا بالاسم (name) + كلمة مرور
# البوابة (portal_password_hash) ويشترط منحه صلاحية «فحص كروت» (cards.check)
# من نموذج الموزّع. البوابة GET فقط — لا يوجد أي مسار عمليات (فصل/قفل/تعطيل
# … إلخ) تحت /portal/distributor إطلاقًا، فالقراءة-فقط مضمونة بنيويًا لا
# بإخفاء أزرار.
DISTRIBUTOR_CHECK_PERM = "cards.check"


def _portal_distributor() -> dict | None:
    """صفّ الموزّع المسجَّل حاليًا، مع إعادة التحقق من الحالة والصلاحية كل
    طلب — تعطيل الموزّع أو سحب الصلاحية يقطع الجلسة فورًا."""
    distributor_id = session.get("portal_distributor_id")
    if not distributor_id:
        return None
    from ..db.repos import operations_repo
    row = operations_repo.get_distributor(_tenant_id(), int(distributor_id))
    if not row or (row.get("status") or "") != "active":
        return None
    if DISTRIBUTOR_CHECK_PERM not in (row.get("permissions_json") or []):
        return None
    return row


def distributor_login():
    if request.method == "POST":
        from werkzeug.security import check_password_hash

        from ..db.repos import operations_repo
        from ..services.login_events import record_login_event

        name = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        row = operations_repo.get_distributor_by_login(1, name)
        ok = bool(row) and check_password_hash(
            row.get("portal_password_hash") or "", password
        )
        allowed = bool(row) and DISTRIBUTOR_CHECK_PERM in (
            row.get("permissions_json") or []
        )
        if not (ok and allowed):
            record_login_event(
                actor_type="distributor", username=name, success=False,
                reason=("no_permission" if (ok and not allowed) else "bad_password"),
                tenant_id=1, attempted_password=password,
            )
            flash("بيانات الدخول غير صحيحة أو صلاحية «فحص كروت» غير مفعّلة.", "error")
            return render_template("radius/portal_distributor_login.html"), 401
        record_login_event(actor_type="distributor", username=name,
                           success=True, actor_id=row.get("id"), tenant_id=1)
        session["portal_tenant_id"] = 1
        session["portal_distributor_id"] = int(row["id"])
        session.pop("portal_subscriber_id", None)
        session.pop("portal_card_user_id", None)
        return redirect(url_for("portal.distributor_home"))
    if _portal_distributor():
        return redirect(url_for("portal.distributor_home"))
    return render_template("radius/portal_distributor_login.html")


def distributor_logout():
    session.pop("portal_distributor_id", None)
    flash("تم تسجيل الخروج من بوابة الموزّع.", "info")
    return redirect(url_for("portal.distributor_login"))


def distributor_home():
    distributor = _portal_distributor()
    if not distributor:
        session.pop("portal_distributor_id", None)
        return redirect(url_for("portal.distributor_login"))
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    result = None
    error = ""
    if query:
        if len(query) > 128:
            error = "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا."
        else:
            from ..services.card_checker import check_card
            try:
                full = check_card(_tenant_id(), query)
            except Exception:  # noqa: BLE001 — لا 500 في وجه الموزّع
                import logging
                logging.getLogger(__name__).exception(
                    "distributor portal: check_card raised for query=%r", query
                )
                error = "حدث خطأ داخلي أثناء الفحص. حاول مجددًا أو راجع المزوّد."
                full = None
            if full is not None:
                if full.get("exists") and not _distributor_scope_allows(distributor, full):
                    # خارج نطاق الموزّع: لا نكشف أي تفاصيل عن الكرت —
                    # رسالة صريحة أن الكرت ليس ضمن حزمه المسموحة.
                    result = {"exists": False, "out_of_scope": True}
                else:
                    result = _distributor_check_view(full)
    return render_template(
        "radius/portal_distributor_checker.html",
        distributor=distributor,
        query=query,
        result=result,
        error=error,
        scope_all=_distributor_scope_is_all(distributor),
    )


def _distributor_scope_is_all(distributor: dict) -> bool:
    """نطاق الفحص: 'all' = كل الحزم؛ أي قيمة أخرى (الافتراض 'assigned')
    = الحزم المربوطة بالموزّع فقط."""
    scope = distributor.get("scope_json") or {}
    return str(scope.get("card_batches") or "assigned").strip().lower() == "all"


def _distributor_scope_allows(distributor: dict, full: dict) -> bool:
    """إنفاذ خادميّ لنطاق الفحص — كرت بلا حزمة أو بحزمة غير مربوطة يُرفض
    في وضع «حزم معيّنة»."""
    if _distributor_scope_is_all(distributor):
        return True
    batch = full.get("batch") or {}
    batch_id = batch.get("id")
    if not batch_id:
        return False
    from ..db.repos import operations_repo
    return operations_repo.is_batch_assigned_to_distributor(
        _tenant_id(), int(distributor["id"]), int(batch_id)
    )


def _distributor_check_view(full: dict) -> dict:
    """إسقاط قراءة-فقط من حمولة check_card الكاملة لبوابة الموزّع.

    قائمة سماح صريحة: لا operations ولا كلمات مرور ولا بيانات المشترك
    الشخصية (الجوال) ولا تفاصيل الجلسات/الأجهزة الخام — فقط ما يلزم
    الموزّع ليجيب زبونه: هل الكرت موجود/شغّال، وقته، واستهلاكه العام."""
    if not full.get("exists"):
        return {"exists": False, "query": full.get("query") or ""}
    summary = full.get("accounting_summary") or {}
    batch = full.get("batch") or {}
    profile = full.get("profile") or {}
    assigned = full.get("assigned_to") or {}
    return {
        "exists": True,
        "status": full.get("status") or "",
        "username": full.get("username") or "",
        "batch_name": batch.get("name") or batch.get("code") or "",
        "plan_name": profile.get("name") or "",
        "accounting_mode": full.get("accounting_mode") or "",
        "created_at": full.get("created_at"),
        "started_at": full.get("started_at"),
        "expires_at": full.get("expires_at"),
        "remaining_seconds": full.get("remaining_seconds"),
        "budget_seconds": full.get("accounting_budget_seconds"),
        "last_seen_at": full.get("last_seen_at"),
        "online_now": bool(int(summary.get("online_sessions") or 0) > 0),
        "sessions_count": int(summary.get("sessions_count") or 0),
        "total_session_seconds": int(summary.get("total_session_seconds") or 0),
        "total_bytes": (
            int(summary.get("total_upload_bytes") or 0)
            + int(summary.get("total_download_bytes") or 0)
        ),
        "assigned_username": assigned.get("username") or "",
        "disabled_reason": full.get("disabled_reason") or "",
        "devices": _distributor_devices(summary.get("macs") or []),
    }


def _mask_mac(mac: str) -> str:
    """يكشف نصف الماك فقط: أوّل ثلاث خانات (تحدّد الشركة) والباقي مُقنَّع.

    قرار المالك: «الماك مش مكشوف، نصّه بيكفي» — يكفي الموزّع ليميّز الأجهزة
    ويعرف نوعها دون تسليمه معرّفًا كاملًا قابلًا للانتحال/التتبّع."""
    raw = (mac or "").strip().upper().replace("-", ":")
    parts = [p for p in raw.split(":") if p]
    if len(parts) < 4:
        return "••:••:••"
    return ":".join(parts[:3]) + ":••:••:••"


def _distributor_devices(macs: list) -> list:
    """أجهزة فتحت البطاقة — نوع/اسم الجهاز + ماك مُقنَّع + نشاطه.

    المصدر نفسه الذي تعرضه غرفة عمليات البطاقة: بصمة DHCP (اسم الجهاز
    ونظامه) وإلّا استنتاج الشركة من الماك."""
    out = []
    for item in macs:
        mac = item.get("mac") or ""
        if not mac:
            continue
        dev = item.get("device") or {}
        dhcp = item.get("dhcp_device") or {}
        name = (dhcp.get("label") or "").strip()
        if not name:
            brand = (dhcp.get("brand") or "").strip()
            model = (dhcp.get("model") or "").strip()
            name = " ".join(x for x in (brand, model) if x).strip()
        if not name:
            name = (dev.get("label") or dev.get("vendor") or "").strip()
        out.append({
            "mac_masked": _mask_mac(mac),
            "name": name or "جهاز غير معروف",
            "vendor": (dev.get("vendor") or "").strip(),
            "icon": dev.get("icon") or "mobile-screen-button",
            "is_random_mac": bool(dev.get("is_random_mac")),
            "sessions_count": int(item.get("sessions_count") or 0),
            "online": int(item.get("online_sessions") or 0) > 0,
            "last_seen_at": item.get("last_seen_at"),
        })
    return out


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


# ── «انتهى اشتراكك» — public subscription-expired / renew page (phase 2) ──────
# Works for ALL three subscriber types (cards / PPPoE / hotspot): the router's
# firewall confines an expired user (placed by RADIUS into hr-pool-expired) to
# the walled garden and redirects their HTTP here. Generic by default; if the
# redirect carries ?u=<username> (a hotspot walled-garden redirect can append
# it) we show the username. Everything (title/message/renew link/contact/logo)
# is panel-configurable with sensible Arabic defaults. Served over HTTP — a
# captive redirect cannot transparently intercept HTTPS.
def subscription_expired():
    from flask import make_response
    from ..core import env_settings

    def _s(key: str, default: str = "") -> str:
        return str(env_settings.env(key, default) or default).strip()

    username = (request.args.get("u") or request.args.get("user") or "").strip()
    ctx = {
        "title":   _s("HOBERADIUS_BLOCK_PAGE_TITLE", "انتهى اشتراكك"),
        "message": _s("HOBERADIUS_BLOCK_PAGE_MESSAGE",
                      "انتهت صلاحية اشتراكك. جدّد الآن لاستعادة الخدمة."),
        "renewal_link": _s("HOBERADIUS_BLOCK_PAGE_RENEWAL_LINK"),
        "phone":        _s("HOBERADIUS_BLOCK_PAGE_CONTACT_PHONE"),
        "whatsapp":     _s("HOBERADIUS_BLOCK_PAGE_CONTACT_WHATSAPP"),
        "logo_url":     _s("HOBERADIUS_BLOCK_PAGE_LOGO_URL"),
        "username":     username[:64],
    }
    resp = make_response(render_template("radius/subscription_expired.html", **ctx))
    # captive pages must never be cached (the user renews → must re-fetch state)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp, 200
