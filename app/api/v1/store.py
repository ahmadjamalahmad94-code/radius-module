"""متجر المايكروتيك — API عام لمستخدمي البطاقات (صفحة store.html على الراوتر).

المايكروتيك لا يتعامل جيدًا مع صفحات الويب الديناميكية، لذلك صفحة
المتجر ملف HTML واحد يُرفع إلى ملفات الهوت سبوت على الراوتر نفسه
ويتخاطب مع سيرفر الراديوس عبر هذه النقاط فقط (fetch + توكن موقّع).

النقاط (كلها تحت /api/v1/store/*):

  POST /store/login     {mobile, password}            → {token, ttl, card_user}
  GET  /store/me                                       → المحفظة + البطاقات + السجل
  GET  /store/packages                                 → باقات السوق النشطة
  GET  /store/my-cards  ?page=&per_page=               → بطاقات الزبون + الحالة من الراديوس
  GET  /store/purchases ?page=&per_page=               → سجل مشترياته مصفّحًا
  POST /store/redeem    {card_number, card_password}   → شحن المحفظة ببطاقة شحن
  POST /store/purchase  {package_id}                   → شراء باقة + بيانات الكرت

الأمان:
  - الدخول بجوال + كلمة مرور بوابة مستخدم البطاقة (نفس مصادقة
    CustomerPortalService.authenticate_card_user — لا منطق جديد).
  - بعد الدخول: توكن itsdangerous موقّع قصير العمر (انظر
    services/store_token.py) في ترويسة Authorization: Bearer.
  - كل نقطة محمية ترى مستخدم البطاقة المضمَّن في التوكن فقط — لا
    يمكن تمرير card_user_id من العميل إطلاقًا.
  - كبح محاولات الدخول: 10 محاولات فاشلة لكل (جوال + IP) في
    الدقيقة ثم 429 — يصد التخمين من شبكة الهوت سبوت.

CORS:
  صفحة المتجر تعمل من أصل (origin) الراوتر — IP غير معروف مسبقًا
  ويختلف من شبكة لأخرى، لذلك نقاط /store/* تعيد
  Access-Control-Allow-Origin: * صراحة (بعد سياسة CORS العامة في
  app/__init__.py التي قد تفشل مغلقةً في الإنتاج). هذا آمن لأن
  المصادقة بتوكن في الترويسة لا بكوكيز — لا credentials تُرسل
  تلقائيًا فلا CSRF عبر الأصول.
"""
from __future__ import annotations

import functools
import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.services.business_os_finance import minor_to_money
from ...radius.services.card_users_marketplace import (
    CardMarketplaceError,
    CardUsersMarketplaceService,
)
from ...radius.services.customer_portals import (
    CustomerPortalService,
    PortalAuthError,
)
from ...radius.services.store_token import (
    StoreTokenError,
    issue_store_token,
    token_ttl_seconds,
    verify_store_token,
)
from ..responses import fail, ok

_LOG = logging.getLogger(__name__)

# ───────────────────────── كبح محاولات الدخول ─────────────────────────
# في الذاكرة (مثل rate limit الـ API العادي) — يكفي لصد تخمين كلمات
# المرور من شبكة الهوت سبوت دون أي بنية إضافية.
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_FAILURES = 10
_login_lock = Lock()
_login_failures: dict[str, deque] = defaultdict(deque)


def _login_throttled(key: str) -> bool:
    """True عندما تجاوز المفتاح حد المحاولات الفاشلة في النافذة."""
    now = time.monotonic()
    with _login_lock:
        log = _login_failures[key]
        while log and (now - log[0]) > _LOGIN_WINDOW_SECONDS:
            log.popleft()
        return len(log) >= _LOGIN_MAX_FAILURES


def _record_login_failure(key: str) -> None:
    with _login_lock:
        _login_failures[key].append(time.monotonic())


# ───────────────────────── كبح التسجيل الذاتي ─────────────────────────
# التسجيل ينشئ حسابًا فعّالًا بلا تأكيد إداري، فنكبحه بحزم لكل IP:
# 5 محاولات لكل 10 دقائق ثم 429 — يصد إنشاء حسابات آلي مسيء من شبكة
# الهوت سبوت دون إزعاج الزبون العادي (يسجّل مرة واحدة).
_REGISTER_WINDOW_SECONDS = 600.0
_REGISTER_MAX = 5
_register_lock = Lock()
_register_attempts: dict[str, deque] = defaultdict(deque)


def _register_throttled(key: str) -> bool:
    now = time.monotonic()
    with _register_lock:
        log = _register_attempts[key]
        while log and (now - log[0]) > _REGISTER_WINDOW_SECONDS:
            log.popleft()
        return len(log) >= _REGISTER_MAX


def _record_register_attempt(key: str) -> None:
    with _register_lock:
        _register_attempts[key].append(time.monotonic())


# ───────────────────────── تسجيل النقاط + CORS ─────────────────────────


def register(bp: Blueprint) -> None:
    routes = [
        # فحص اتصال خفيف بلا توكن — يستدعيه الفحص الذاتي في store.html
        # وزر «اختبار الاتصال» في المصمّم للتأكد أن العنوان المحقون
        # قابل للوصول فعلاً قبل أي محاولة دخول.
        ("/store/ping", "store_ping", store_ping, ["GET"]),
        ("/store/register", "store_register", store_register, ["POST"]),
        ("/store/login", "store_login", store_login, ["POST"]),
        ("/store/me", "store_me", _require_store_token(store_me), ["GET"]),
        ("/store/packages", "store_packages",
         _require_store_token(store_packages), ["GET"]),
        ("/store/my-cards", "store_my_cards",
         _require_store_token(store_my_cards), ["GET"]),
        ("/store/purchases", "store_purchases",
         _require_store_token(store_purchases), ["GET"]),
        ("/store/redeem", "store_redeem",
         _require_store_token(store_redeem), ["POST"]),
        ("/store/purchase", "store_purchase",
         _require_store_token(store_purchase), ["POST"]),
    ]
    for rule, endpoint, view, methods in routes:
        bp.add_url_rule(rule, endpoint, view, methods=methods)


def _apply_store_cors(resp):
    """يضع ترويسات CORS المفتوحة على رد نقطة متجر — مصدر واحد
    يستدعيه كل من preflight (before_request) وردود النقاط (after_request)
    فلا تتفرّق الترويسات."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type"
    )
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Max-Age"] = "3600"
    # الرد عام لأي أصل — لا تخزين مشروط بالأصل.
    resp.headers.pop("Vary", None)
    return resp


def install_store_cors(app) -> None:
    """CORS مفتوح + preflight مضمون لنقاط /api/v1/store/* فقط.

    صفحة المتجر أصلها IP الراوتر (مجهول مسبقًا) والمصادقة بتوكن في
    الترويسة — السماح لأي أصل هنا آمن ولا يمس بقية الـ API.

    طبقتان:
      1) before_request: أي طلب OPTIONS لمسار /api/v1/store/* يُحسم
         فورًا بـ204 + ترويسات CORS كاملة — **قبل** أي توجيه أو تحقق
         توكن. هذا يضمن نجاح الـ preflight الذي يرسله المتصفح قبل
         POST بـ Content-Type: application/json من أصل الراوتر، ويمنع
         أي 405 (مثل ارتطام الطلب بمسار /api/<path> العام المخصّص
         لـOPTIONS وحده). يسبق فحص التوكن فلا يُحجب preflight بـ401.
      2) after_request: يضع نفس الترويسات على كل ردود النقاط الفعلية
         (login/ping/me/…) فيقرأ المتصفح الرد عبر الأصول.
    """
    @app.before_request
    def _store_cors_preflight():  # noqa: ANN202
        if (request.method == "OPTIONS"
                and request.path.startswith("/api/v1/store/")):
            from flask import make_response
            return _apply_store_cors(make_response("", 204))
        return None

    @app.after_request
    def _store_cors_headers(resp):  # noqa: ANN001
        if request.path.startswith("/api/v1/store/"):
            _apply_store_cors(resp)
        return resp


def install_store_key_guard(app) -> None:
    """بوّابة مفتاح التطبيق لنقاط /api/v1/store/* — تمنع أي طرف لا
    يحمل مفتاح المتجر الصحيح من استدعاء الـ API (انظر services/store_key).

    ترتيب الطبقات مقصود:
      • تعمل **بعد** preflight (install_store_cors): طلب OPTIONS لا يحمل
        ترويسات مخصّصة فلا يُفحص هنا — وإلا حجبنا الـ preflight بـ403
        وفشل كل نداء من المتصفح. لذلك نتجاهل OPTIONS صراحةً.
      • تعمل **قبل** تحقق توكن الجلسة (_require_store_token): المفتاح
        بوّابة التطبيق أولًا (من يصل أصلًا)، ثم التوكن هوية المستخدم.
      • الرد 403 يمرّ على after_request فيحمل ترويسات CORS — فيقرأ
        المتصفح رسالة الخطأ بدل حجبها صامتةً.

    التوافق: قبل أول نشر للمتجر لا يوجد مفتاح مخزَّن فلا فرض (verify
    تعيد True) — كل الاختبارات والمسارات القائمة تعمل دون مفتاح حتى
    يُولَّد واحد عند النشر."""
    @app.before_request
    def _store_key_guard():  # noqa: ANN202
        if not request.path.startswith("/api/v1/store/"):
            return None
        if request.method == "OPTIONS":
            return None
        from ...radius.services.store_key import (
            STORE_KEY_HEADER, verify_store_key,
        )
        tid = int(getattr(g, "tenant_id", 1) or 1)
        if not verify_store_key(request.headers.get(STORE_KEY_HEADER, ""), tid):
            return fail(
                "store_key_invalid",
                "هذا الطلب لا يحمل مفتاح المتجر الصحيح — استخدم متجرك "
                "المنشور لا أداة خارجية.",
                status=403,
            )
        return None


# ───────────────────────── أدوات داخلية ─────────────────────────


def _payload() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For") or
            request.remote_addr or "?").split(",")[0].strip()


def _extract_store_token() -> str:
    h = request.headers.get("Authorization") or ""
    if h.lower().startswith("bearer "):
        return h.split(None, 1)[1].strip()
    return ""


def _require_store_token(view):
    """يفك توكن المتجر ويضع هوية مستخدم البطاقة في g — أو 401."""
    @functools.wraps(view)
    def wrapped(*a, **kw):
        try:
            ident = verify_store_token(_extract_store_token())
        except StoreTokenError as exc:
            return fail(exc.code, exc.message_ar, status=401)
        g.store_card_user_id = int(ident["card_user_id"])
        g.store_tenant_id = int(ident["tenant_id"])
        return view(*a, **kw)
    return wrapped


def _tid() -> int:
    return int(getattr(g, "store_tenant_id", 1))


def _cuid() -> int:
    return int(getattr(g, "store_card_user_id", 0))


def _marketplace() -> CardUsersMarketplaceService:
    return CardUsersMarketplaceService(tenant_id=_tid())


def _portal() -> CustomerPortalService:
    return CustomerPortalService(tenant_id=_tid())


def _money(minor: Any) -> str:
    return minor_to_money(minor)


def _public_wallet(wallet: dict[str, Any]) -> dict[str, Any]:
    """شكل المحفظة الذي تعرضه صفحة المتجر — أرقام جاهزة للعرض."""
    return {
        "balance": _money(wallet.get("balance_minor")),
        "balance_minor": int(wallet.get("balance_minor") or 0),
        "currency": str(wallet.get("currency") or ""),
        "status": str(wallet.get("status") or "active"),
    }


def _public_card_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(user.get("id") or 0),
        "display_name": str(user.get("display_name") or ""),
        "mobile": str(user.get("mobile") or ""),
        "status": str(user.get("status") or "active"),
    }


_AVAILABILITY_AR = {
    "available": "متوفر",
    "limited": "كمية محدودة",
    "out": "نفد",
}


def _public_package(pkg: dict[str, Any]) -> dict[str, Any]:
    """باقة السوق كما يراها الزبون — بلا metadata داخلية ولا تكاليف.

    عروض المخزون لا تكشف العدد الدقيق للمشتري عندما يكون كبيرًا —
    فقط تدرّج التوفر: متوفر / كمية محدودة (<10% أو ≤10) / نفد.
    العدد الدقيق يُرسل فقط عندما يكون صغيرًا (≤10) ليفيد الزبون.
    """
    sale_mode = str(pkg.get("sale_mode") or "instant")
    remaining = None
    availability = "available"  # التوليد الفوري متوفر دائمًا
    if sale_mode == "inventory":
        total = int(pkg.get("inventory_total") or 0)
        remaining = max(0, total - int(pkg.get("inventory_sold") or 0))
        if remaining <= 0:
            availability = "out"
        elif remaining <= 10 or (total > 0 and remaining * 10 < total):
            availability = "limited"
    return {
        "id": int(pkg.get("id") or 0),
        "name": str(pkg.get("name") or ""),
        "price": _money(pkg.get("price_minor")),
        "price_minor": int(pkg.get("price_minor") or 0),
        "currency": str(pkg.get("currency") or ""),
        "duration_minutes": int(pkg.get("display_duration_minutes")
                                or pkg.get("duration_minutes") or 0),
        "speed_down_kbps": int(pkg.get("display_speed_down_kbps")
                               or pkg.get("speed_down_kbps") or 0),
        "speed_up_kbps": int(pkg.get("display_speed_up_kbps")
                             or pkg.get("speed_up_kbps") or 0),
        "quota_total_mb": int(pkg.get("plan_quota_total_mb") or 0),
        "card_color": str(pkg.get("card_color") or "#14b8a6"),
        "sale_mode": sale_mode,
        # تدرّج التوفر بدل العدد الدقيق — العدد يُكشف فقط عندما يكون ≤10.
        "availability": availability,
        "availability_ar": _AVAILABILITY_AR.get(availability, availability),
        "stock_remaining": (remaining if (remaining is not None and remaining <= 10) else None),
        "in_stock": (remaining is None or remaining > 0),
    }


def _purchase_history(card_user_id: int, *, limit: int = 25) -> list[dict[str, Any]]:
    """سجل مشتريات الزبون مع بيانات الكرت — الزبون يرى كروته هو فقط
    (الاستعلام مقيد بـ card_user_id من التوكن)."""
    from ...radius.db.connection import db
    from ...radius.db.helpers import row_to_dict

    rows = db().execute(
        """
        SELECT cup.id            AS purchase_id,
               cup.created_at    AS created_at,
               cup.amount_minor  AS amount_minor,
               cup.currency      AS currency,
               cup.status        AS status,
               p.name            AS package_name,
               c.username        AS card_username,
               c.password        AS card_password,
               c.used            AS card_used
        FROM card_user_purchases cup
        LEFT JOIN card_marketplace_packages p
          ON p.tenant_id = cup.tenant_id AND p.id = cup.package_id
        LEFT JOIN cards c
          ON c.tenant_id = cup.tenant_id AND c.id = cup.card_id
        WHERE cup.tenant_id = ? AND cup.card_user_id = ?
        ORDER BY cup.id DESC
        LIMIT ?
        """,
        (_tid(), int(card_user_id), int(limit)),
    ).fetchall()
    out = []
    for row in rows:
        item = row_to_dict(row)
        item["amount"] = _money(item.pop("amount_minor", 0))
        out.append(item)
    return out


def _page_args() -> tuple[int, int, int]:
    """صفحة وحجمها من سلسلة الاستعلام — حدود آمنة (نفس منطق
    CardUsersMarketplaceService._page_args)."""
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = max(1, min(50, int(request.args.get("per_page") or 20)))
    except (TypeError, ValueError):
        per_page = 20
    return page, per_page, (page - 1) * per_page


def _card_state(card: dict[str, Any]) -> str:
    """حالة البطاقة كما تشتقها بوابة الزبون (portal_card.html) من
    أعمدة cards نفسها + جلسات radacct الحيّة:

      revoked  → ملغاة
      expire_at في الماضي → expired (منتهية)
      used=0   → unused (غير مستخدمة بعد)
      used=1 وجلسة حيّة (acctstoptime IS NULL) → active (فعالة الآن)
      used=1 بلا جلسة حيّة → consumed (مستهلكة)

    المقارنة الزمنية نصية لأن كل التواريخ ISO-8601 UTC من now_iso().
    """
    from ...radius.db.helpers import now_iso

    if int(card.get("revoked") or 0):
        return "revoked"
    # توحيد فاصل التاريخ/الوقت (بعض المسارات تخزن "YYYY-MM-DD HH:MM")
    # حتى تصح المقارنة النصية مع صيغة now_iso (بفاصل T).
    expire_at = str(card.get("expire_at") or "").replace(" ", "T")
    if expire_at and expire_at[:19] < now_iso()[:19]:
        return "expired"
    if not int(card.get("used") or 0):
        return "unused"
    if int(card.get("online_sessions") or 0) > 0:
        return "active"
    return "consumed"


# نصوص الحالات بالعربية — تُرسل جاهزة فلا تترجم الصفحة شيئًا.
_CARD_STATE_AR = {
    "active": "فعالة الآن",
    "unused": "غير مستخدمة بعد",
    "expired": "منتهية",
    "consumed": "مستهلكة",
    "revoked": "ملغاة",
}


# ───────────────────────── النقاط ─────────────────────────


def store_ping():
    """فحص اتصال خفيف — بلا توكن وبلا أي لمسة لقاعدة البيانات.

    الهدف الجذري: تمييز «العنوان غير قابل للوصول» (فشل شبكة/منفذ
    خاطئ) عن «وصلنا للخادم» بوضوح. صفحة المتجر (الفحص الذاتي) وزر
    «اختبار الاتصال» في المصمّم ينادِيانها قبل تسجيل الدخول؛ نجاحها
    يعني أن العنوان+المنفذ المحقونين في store.html صحيحان وأن
    walled-garden يسمح بالوصول، ففشل الدخول لاحقًا لن يُنسب للشبكة.

    ترجع {ok:true, data:{service, time, version}} عبر مساعد ok()
    فتحمل ترويسات CORS المفتوحة (install_store_cors) لأي أصل."""
    from datetime import datetime, timezone
    return ok({
        "service": "store",
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
    })


def store_register():
    """تسجيل ذاتي لزبون جديد من المتجر — اسم ثلاثي + جوال + كلمة مرور.

    ينشئ حساب مستخدم بطاقة **فعّالًا فورًا** (بمحفظة) بلا أي تأكيد
    إداري، ثم يسجّل دخوله تلقائيًا (يعيد توكنًا) فيشحن ويشتري مباشرة.
    كلمة المرور تُهشَّم بنفس آلية مستخدمي البطاقات.

    الحماية: كبح معدّل لكل IP (5/10د)، تطبيع وفحص صيغة الجوال، ومنع
    تكرار رقم جوال نشط — كلها في الخدمة/النقطة لا في الواجهة فقط.

    tenant_id=1 مطابقةً لنقطة الدخول (store_login) وبقية نقاط المتجر
    التي تعمل على المستأجر الافتراضي."""
    body = _payload()
    name = str(body.get("display_name") or "").strip()
    mobile = str(body.get("mobile") or "").strip()
    password = str(body.get("password") or "")
    if not name or not mobile or not password:
        return fail(
            "validation_error",
            "أدخل الاسم الثلاثي ورقم الجوال وكلمة المرور.",
            status=422,
        )
    if _register_throttled(_client_ip()):
        return fail(
            "rate_limited",
            "محاولات تسجيل كثيرة — انتظر قليلًا ثم حاول مجددًا.",
            status=429,
            details={"retry_after_seconds": int(_REGISTER_WINDOW_SECONDS)},
        )
    _record_register_attempt(_client_ip())
    try:
        user = CardUsersMarketplaceService(tenant_id=1).register_card_user(
            display_name=name, mobile=mobile, password=password,
        )
    except CardMarketplaceError as exc:
        return fail("register_failed", str(exc) or "تعذّر إنشاء الحساب.",
                    status=422)
    # سجّل حدث دخول (نجاح) — نفس ما يفعله store_login، لا يكسر التسجيل.
    try:
        from ...radius.services.login_events import record_login_event
        record_login_event(actor_type="card",
                           username=str(user.get("mobile") or mobile),
                           success=True, actor_id=user.get("id"),
                           tenant_id=1)
    except Exception:  # noqa: BLE001
        pass
    # دخول تلقائي فور التسجيل — نفس توكن store_login.
    token = issue_store_token(card_user_id=int(user["id"]), tenant_id=1)
    return ok({
        "token": token,
        "token_ttl_seconds": token_ttl_seconds(),
        "card_user": _public_card_user(user),
    }, status=201)


def store_login():
    """دخول الزبون: جوال + كلمة مرور → توكن موقّع قصير العمر."""
    body = _payload()
    mobile = str(body.get("mobile") or "").strip()
    password = str(body.get("password") or "")
    if not mobile or not password:
        return fail(
            "validation_error",
            "أدخل رقم الجوال وكلمة المرور.",
            status=422,
        )
    throttle_key = f"{mobile}|{_client_ip()}"
    if _login_throttled(throttle_key):
        return fail(
            "rate_limited",
            "محاولات كثيرة — انتظر دقيقة ثم حاول مجددًا.",
            status=429,
            details={"retry_after_seconds": 60},
        )
    try:
        # نفس مصادقة بوابة البطاقات تمامًا (hash + check_password_hash).
        user = CustomerPortalService(tenant_id=1).authenticate_card_user(
            mobile=mobile, password=password,
        )
    except PortalAuthError:
        _record_login_failure(throttle_key)
        try:
            from ...radius.services.login_events import record_login_event
            record_login_event(actor_type="card", username=mobile,
                               success=False, reason="bad_password",
                               tenant_id=1)
        except Exception:  # noqa: BLE001 — السجل لا يكسر الدخول أبدًا
            pass
        return fail(
            "invalid_credentials",
            "رقم الجوال أو كلمة المرور غير صحيحة.",
            status=401,
        )
    try:
        from ...radius.services.login_events import record_login_event
        record_login_event(actor_type="card",
                           username=str(user.get("mobile") or mobile),
                           success=True, actor_id=user.get("id"),
                           tenant_id=1)
    except Exception:  # noqa: BLE001
        pass
    token = issue_store_token(card_user_id=int(user["id"]), tenant_id=1)
    return ok({
        "token": token,
        "token_ttl_seconds": token_ttl_seconds(),
        "card_user": _public_card_user(user),
    })


def store_me():
    """لوحة الزبون: المحفظة + بياناته + باقات السوق + سجل مشترياته."""
    svc = _marketplace()
    try:
        user = svc.get_card_user(_cuid())
    except CardMarketplaceError:
        return fail("not_found", "الحساب غير موجود.", status=404)
    wallet = svc._wallet_for_card_user(_cuid())  # noqa: SLF001 — نفس استخدام بوابة البطاقات
    return ok({
        "card_user": _public_card_user(user),
        "wallet": _public_wallet(wallet),
        "packages": [_public_package(p)
                     for p in svc.list_packages(active_only=True, limit=100)],
        "purchases": _purchase_history(_cuid()),
    })


def store_packages():
    """معرض الباقات النشطة فقط — بلا أي حقول داخلية."""
    packages = _marketplace().list_packages(active_only=True, limit=100)
    items = [_public_package(p) for p in packages]
    return ok({"items": items, "count": len(items)})


def store_redeem():
    """شحن المحفظة ببطاقة شحن (كود + رقم سري إن وُجد)."""
    body = _payload()
    try:
        result = _portal().redeem_card_to_wallet(
            card_user_id=_cuid(),
            card_number=str(body.get("card_number") or ""),
            card_password=str(body.get("card_password") or ""),
        )
    except (RadiusValidationError, ValueError) as exc:
        return fail("redeem_failed", str(exc) or "تعذر شحن البطاقة.",
                    status=422)
    return ok({
        "amount": result.get("amount"),
        "wallet": _public_wallet(result.get("wallet") or {}),
    }, status=201)


def store_purchase():
    """شراء باقة من السوق — خصم المحفظة وتسليم بيانات الكرت فورًا."""
    body = _payload()
    try:
        package_id = int(body.get("package_id") or 0)
    except (TypeError, ValueError):
        package_id = 0
    if package_id <= 0:
        return fail("validation_error", "اختر باقة أولاً.", status=422)
    svc = _marketplace()
    try:
        purchase = svc.purchase_package(
            card_user_id=_cuid(),
            package_id=package_id,
            actor="mikrotik_store",
        )
    except (CardMarketplaceError, ValueError) as exc:
        msg = str(exc) or "تعذر إتمام الشراء."
        code = "insufficient_balance" if "غير كاف" in msg else "purchase_failed"
        status = 402 if code == "insufficient_balance" else 422
        return fail(code, msg, status=status)
    # بيانات الكرت المُشترى — الزبون يستخدمها للدخول من صفحة الهوت سبوت.
    card = {}
    card_id = int(purchase.get("card_id") or 0)
    if card_id:
        from ...radius.db.connection import db
        row = db().execute(
            "SELECT username, password FROM cards WHERE tenant_id=? AND id=?",
            (_tid(), card_id),
        ).fetchone()
        if row:
            card = {"username": row["username"], "password": row["password"]}
    wallet = svc._wallet_for_card_user(_cuid())  # noqa: SLF001
    return ok({
        "purchase_id": int(purchase.get("id") or 0),
        "amount": purchase.get("amount"),
        "currency": purchase.get("currency"),
        "card": card,
        "wallet": _public_wallet(wallet),
    }, status=201)


def store_my_cards():
    """بطاقات الزبون التي اشتراها من المتجر — تفاصيل كاملة + حالة.

    نفس بيانات تبويب «بطاقاتي» في بوابة الزبون الويب
    (CardUsersMarketplaceService.card_user_360): البطاقات المرتبطة
    بمشترياته فقط (مقيدة بـ card_user_id من التوكن) مع بيانات
    الدخول، مواصفات الباقة من السوق، وحالة مشتقة من أعمدة cards
    وجلسات radacct الحيّة (انظر _card_state).

    البيانات الحساسة (يوزر/باس البطاقة) تُرسل هنا لأن الزبون مالكها
    أصلًا — اشتراها بمحفظته، وهي نفسها التي تظهر له لحظة الشراء.
    """
    from ...radius.db.connection import db
    from ...radius.db.helpers import row_to_dict

    page, per_page, offset = _page_args()
    total = int(db().execute(
        """
        SELECT COUNT(*) AS n FROM card_user_purchases
        WHERE tenant_id=? AND card_user_id=? AND card_id IS NOT NULL
        """,
        (_tid(), _cuid()),
    ).fetchone()["n"])
    rows = db().execute(
        """
        SELECT cup.id           AS purchase_id,
               cup.created_at   AS purchased_at,
               cup.amount_minor AS amount_minor,
               cup.currency     AS currency,
               c.id             AS card_id,
               c.username       AS username,
               c.password       AS password,
               c.used           AS used,
               COALESCE(c.revoked, 0) AS revoked,
               c.first_used_at  AS first_used_at,
               c.expire_at      AS expire_at,
               COALESCE(p.name, b.package_name, '') AS package_name,
               COALESCE(p.metadata_json, '')        AS pkg_metadata_json,
               COALESCE(NULLIF(p.duration_minutes, 0),
                        ap.duration_minutes, 0)     AS duration_minutes,
               COALESCE(NULLIF(p.speed_down_kbps, 0),
                        ap.speed_down_kbps, 0)      AS speed_down_kbps,
               COALESCE(NULLIF(p.speed_up_kbps, 0),
                        ap.speed_up_kbps, 0)        AS speed_up_kbps,
               COALESCE(ap.quota_total_mb, 0)       AS quota_total_mb,
               COALESCE(u.online_sessions, 0)       AS online_sessions,
               COALESCE(u.total_seconds, 0)         AS total_seconds,
               COALESCE(u.down_bytes, 0)            AS download_bytes,
               COALESCE(u.up_bytes, 0)              AS upload_bytes
        FROM card_user_purchases cup
        JOIN cards c
          ON c.tenant_id = cup.tenant_id AND c.id = cup.card_id
        LEFT JOIN card_batches b
          ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        LEFT JOIN card_marketplace_packages p
          ON p.tenant_id = cup.tenant_id AND p.id = cup.package_id
        LEFT JOIN access_plans ap
          ON ap.tenant_id = c.tenant_id AND ap.id = c.plan_id
        LEFT JOIN (
            SELECT username,
                   SUM(CASE WHEN acctstoptime IS NULL
                            THEN 1 ELSE 0 END)          AS online_sessions,
                   SUM(COALESCE(acctsessiontime, 0))    AS total_seconds,
                   SUM(COALESCE(acctoutputoctets, 0))   AS down_bytes,
                   SUM(COALESCE(acctinputoctets, 0))    AS up_bytes
            FROM radacct WHERE tenant_id = ? GROUP BY username
        ) u ON u.username = c.username
        WHERE cup.tenant_id = ? AND cup.card_user_id = ?
          AND cup.card_id IS NOT NULL
        ORDER BY cup.id DESC
        LIMIT ? OFFSET ?
        """,
        (_tid(), _tid(), _cuid(), per_page, offset),
    ).fetchall()
    import json as _jsonlib

    items = []
    for row in rows:
        card = row_to_dict(row)
        state = _card_state(card)
        # لون البطاقة من metadata الباقة — نفس استخلاص _row في
        # CardUsersMarketplaceService.
        try:
            meta = _jsonlib.loads(card.get("pkg_metadata_json") or "{}")
        except (TypeError, ValueError):
            meta = {}
        color = str(meta.get("card_color") or meta.get("color")
                    or "#14b8a6")
        items.append({
            "purchase_id": int(card.get("purchase_id") or 0),
            "card_id": int(card.get("card_id") or 0),
            "package_name": str(card.get("package_name") or "بطاقة"),
            "username": str(card.get("username") or ""),
            "password": str(card.get("password") or ""),
            "price": _money(card.get("amount_minor")),
            "currency": str(card.get("currency") or ""),
            "purchased_at": str(card.get("purchased_at") or ""),
            "first_used_at": str(card.get("first_used_at") or ""),
            "expire_at": str(card.get("expire_at") or ""),
            "duration_minutes": int(card.get("duration_minutes") or 0),
            "speed_down_kbps": int(card.get("speed_down_kbps") or 0),
            "speed_up_kbps": int(card.get("speed_up_kbps") or 0),
            "quota_total_mb": int(card.get("quota_total_mb") or 0),
            "card_color": color,
            "online_now": int(card.get("online_sessions") or 0) > 0,
            "total_minutes": int(card.get("total_seconds") or 0) // 60,
            "download_bytes": int(card.get("download_bytes") or 0),
            "upload_bytes": int(card.get("upload_bytes") or 0),
            "state": state,
            "state_ar": _CARD_STATE_AR.get(state, state),
            # الدخول التلقائي للبطاقات الصالحة للاستخدام فقط.
            "can_login": state in ("active", "unused", "consumed"),
        })
    return ok({
        "items": items,
        "page": page, "per_page": per_page, "total": total,
        "pages": max(1, (total + per_page - 1) // per_page),
    })


def store_purchases():
    """سجل عمليات شراء الزبون مصفّحًا — ماذا اشترى ومتى وبكم وما
    حالة العملية والبطاقة الناتجة (بلا كلمة المرور — التفاصيل
    الكاملة في /store/my-cards)."""
    from ...radius.db.connection import db
    from ...radius.db.helpers import row_to_dict

    page, per_page, offset = _page_args()
    total = int(db().execute(
        "SELECT COUNT(*) AS n FROM card_user_purchases"
        " WHERE tenant_id=? AND card_user_id=?",
        (_tid(), _cuid()),
    ).fetchone()["n"])
    rows = db().execute(
        """
        SELECT cup.id            AS purchase_id,
               cup.created_at    AS created_at,
               cup.amount_minor  AS amount_minor,
               cup.currency      AS currency,
               cup.status        AS status,
               COALESCE(p.name, '')      AS package_name,
               COALESCE(c.username, '')  AS card_username,
               COALESCE(c.used, 0)       AS card_used,
               COALESCE(c.revoked, 0)    AS card_revoked
        FROM card_user_purchases cup
        LEFT JOIN card_marketplace_packages p
          ON p.tenant_id = cup.tenant_id AND p.id = cup.package_id
        LEFT JOIN cards c
          ON c.tenant_id = cup.tenant_id AND c.id = cup.card_id
        WHERE cup.tenant_id = ? AND cup.card_user_id = ?
        ORDER BY cup.id DESC
        LIMIT ? OFFSET ?
        """,
        (_tid(), _cuid(), per_page, offset),
    ).fetchall()
    status_ar = {"completed": "مكتملة", "failed": "فاشلة", "voided": "ملغاة"}
    items = []
    for row in rows:
        item = row_to_dict(row)
        st = str(item.get("status") or "completed")
        items.append({
            "purchase_id": int(item.get("purchase_id") or 0),
            "created_at": str(item.get("created_at") or ""),
            "amount": _money(item.get("amount_minor")),
            "currency": str(item.get("currency") or ""),
            "status": st,
            "status_ar": status_ar.get(st, st),
            "package_name": str(item.get("package_name") or "باقة"),
            "card_username": str(item.get("card_username") or ""),
            "card_used": bool(int(item.get("card_used") or 0)),
            "card_revoked": bool(int(item.get("card_revoked") or 0)),
        })
    return ok({
        "items": items,
        "page": page, "per_page": per_page, "total": total,
        "pages": max(1, (total + per_page - 1) // per_page),
    })
