"""
مصادقة الـ API + rate limit. كل نقطة إدارية محمية بـ `require_api_token`
الذي يقبل **أحد** اعتمادين (وإلا 401):

أ. مفتاح/توكن API — عبر `Authorization: Bearer <token>` أو ترويسة
   `X-API-Key` أو (لـ SSE فقط على GET) `?api_token=`. مصادره:
     1. `HOBERADIUS_API_TOKENS` env (CSV) — tenant_id=1.
     2. DB `api_tokens` — لها tenant_id ومجال (scopes) و expires_at.
     3. dev fallback `dev-token-please-change` — **فقط** خارج الإنتاج.
ب. اعتماد أدمن — عبر `Authorization: Basic base64(user:pass)` بيوزر/باس
   أدمن صحيحَين (يُتحقّق منهما مقابل جدول admins بنفس هاش الدخول). بديلٌ
   لمفتاح الـAPI كما طلب المالك، كي لا يجلب من يعرف الرابط أي بيانات.
   super_admin يحدّد tenant عبر `X-Tenant-Id` (اختياري).

الفرض على مستويين (إضافي ومتوافق رجعيًا):
  • الديكوريتر `require_api_token` على نقاط محدّدة — فعّال دائمًا.
  • حارس مركزي `install_global_api_auth_guard` يغطّي **كل** مسارات /api
    (شاملاً أي نقطة جديدة لم تُزخرَف، مثل نقاط تطبيق Flutter على الـVPS)
    ويُفعَّل تدريجيًا عبر `api_auth_required()` — افتراضه «معطّل» حتى
    يجهّز المالك المفاتيح ويحدّث التطبيقات ثم يفعّله.

تحديد بيئة الإنتاج:
- `HOBERADIUS_ENV=prod|production` أو `FLASK_ENV=prod|production`.
- في الإنتاج: لا fallback dev token، وأي token مفقود/غير صالح يفشل.

التحقّق من الانتهاء:
- DB tokens بحقل `expires_at` (UTC ISO). لو تجاوز utcnow → 401 token_expired.
- env tokens ليس لها expiry (مُدارة يدويًا).

عند نجاح التحقّق نضع: g.api_token_id, g.tenant_id (override).
"""
from __future__ import annotations

import functools
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from threading import Lock
from typing import Optional

from flask import current_app, g, request

from .responses import fail

_LOG = logging.getLogger(__name__)
_DEV_DEFAULT_TOKEN = "dev-token-please-change"
_DEV_FALLBACK_WARNED = False

# rate limit state (in-memory)
_rate_lock = Lock()
_rate_log: dict[str, deque] = defaultdict(deque)


def _is_production() -> bool:
    """Production when HOBERADIUS_ENV or FLASK_ENV resolves to prod/production.
    Empty/unset → development. Keep the check cheap — called per request."""
    env = (os.environ.get("HOBERADIUS_ENV")
           or os.environ.get("FLASK_ENV")
           or "").strip().lower()
    return env in {"prod", "production"}


def _allowed_env_tokens() -> tuple[str, ...]:
    """Explicit env tokens first, then the dev fallback when **not** in prod.

    In production with no `HOBERADIUS_API_TOKENS` set, returns an empty tuple
    — every request must authenticate against the DB `api_tokens` table or
    fail. This closes the dev-token attack surface on a misconfigured deploy.
    """
    global _DEV_FALLBACK_WARNED
    raw = (os.environ.get("HOBERADIUS_API_TOKENS") or "").strip()
    if raw:
        return tuple(t.strip() for t in raw.split(",") if t.strip())
    if _is_production():
        return ()
    if not _DEV_FALLBACK_WARNED:
        _LOG.warning(
            "DEV-MODE auth fallback engaged (token=%r). "
            "Set HOBERADIUS_ENV=prod and HOBERADIUS_API_TOKENS (or use DB "
            "tokens) before deploying.",
            _DEV_DEFAULT_TOKEN,
        )
        _DEV_FALLBACK_WARNED = True
    return (_DEV_DEFAULT_TOKEN,)


def _extract_bearer() -> Optional[str]:
    """Read the API token from the request.

    Primary source: `Authorization: Bearer <token>` header — the
    canonical pattern used by every regular fetch() call.

    Fallback: `?api_token=<token>` query string. Required for
    `EventSource` (Server-Sent Events) requests, which the browser
    spec doesn't let us attach custom headers to. Only ever read on
    GET requests so a token can't accidentally leak into a POST log
    line via a re-issued form.
    """
    h = request.headers.get("Authorization") or ""
    if h.lower().startswith("bearer "):
        tok = h.split(None, 1)[1].strip()
        if tok:
            return tok
    # ترويسة `X-API-Key` — بديل صريح لمفتاح الـAPI يطلبه بعض العملاء/
    # أدوات التكامل التي لا تستخدم Authorization. يُعامَل تمامًا كتوكن.
    xk = (request.headers.get("X-API-Key") or "").strip()
    if xk:
        return xk
    # Query-string fallback — SSE only. Guard on the method so
    # bearer-via-query is never accepted for state-changing verbs.
    if request.method == "GET":
        q = (request.args.get("api_token") or "").strip()
        if q:
            return q
    return None


def _rate_limit_check(token_key: str, *, per_minute: int = 60) -> bool:
    """يُرجع True لو لا يزال مسموحًا. يستخدم سجل dequeue ثابت لكل token."""
    if current_app.testing or os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if per_minute <= 0: return True
    now = time.monotonic()
    window = 60.0
    with _rate_lock:
        log = _rate_log[token_key]
        while log and (now - log[0]) > window:
            log.popleft()
        if len(log) >= per_minute:
            return False
        log.append(now)
        return True


def _configured_api_rpm(tenant_rpm: int = 60) -> int:
    """Return the effective API rate limit.

    The Android/Windows admin clients make many small authenticated requests
    while refreshing dashboards and operational screens. The default is
    therefore unlimited. Tenant-tier limits are ignored for the management API.

    To intentionally re-enable throttling, operators must set both:
      HOBERADIUS_API_RATE_LIMIT_ENABLED=1
      HOBERADIUS_API_RATE_LIMIT_PER_MINUTE=<positive integer>

    This double opt-in prevents old tenant defaults such as 10 req/min from
    breaking the mobile/desktop management clients.
    """
    enabled = (os.environ.get("HOBERADIUS_API_RATE_LIMIT_ENABLED") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return 0
    raw = os.environ.get("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE")
    if raw is None or raw == "":
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        _LOG.warning(
            "Ignoring invalid HOBERADIUS_API_RATE_LIMIT_PER_MINUTE=%r",
            raw,
        )
        return 0


def _is_expired(expires_at_raw) -> Optional[bool]:
    """Return True if expired, False if still valid, None if no expiry set.
    Malformed `expires_at` is treated as expired — fail closed."""
    if not expires_at_raw:
        return None
    try:
        s = str(expires_at_raw).replace("Z", "")
        exp = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return True
    return datetime.utcnow() > exp


def _resolve_admin_tenant(admin) -> Optional[int]:
    """يحدّد tenant الأدمن لمصادقة Basic **دون أثر جانبي** (لا ينشئ عضوية).

    - super_admin: DEFAULT_TENANT_ID افتراضيًا، أو قيمة `X-Tenant-Id`
      الرقمية إن مُرِّرت (يسمح للمالك بالعمل على أي tenant مباشرة).
    - غيره: أول عضوية tenant له؛ بلا أي عضوية → None (يُرفض الدخول).

    لا نُكرّر منطق bootstrap الموجود في تسجيل الدخول (admin_auth._pick_tenant)
    لأن طلب API لا يجب أن يُنشئ عضويات صامتة.
    """
    try:
        from app.radius.core.tenant import DEFAULT_TENANT_ID
    except Exception:  # noqa: BLE001
        DEFAULT_TENANT_ID = 1
    if getattr(admin, "is_super_admin", False):
        hdr = (request.headers.get("X-Tenant-Id") or "").strip()
        if hdr.isdigit():
            return int(hdr)
        return DEFAULT_TENANT_ID
    try:
        from app.radius.stores.tenants_store import TenantsStore
        tenants = TenantsStore.instance().tenants_for_admin(int(admin.id))
    except Exception:  # noqa: BLE001
        tenants = []
    if tenants:
        return tenants[0].id
    return None


def _verify_admin_basic():
    """مصادقة بديلة عبر **اعتماد الأدمن** (HTTP Basic: يوزر+باس الأدمن).

    كما طلب المالك: أي نداء API إداري يُقبل إمّا بمفتاح API صحيح، وإمّا
    باسم مستخدم وكلمة مرور أدمن صحيحَين مباشرةً في ترويسة Basic — فلا
    يستطيع من يعرف الرابط فقط جلب البيانات.

    التحقّق خفيف الأثر (get_by_username + verify_password) ولا يحدّث
    last_login على كل طلب (تجنّب الضجيج/البطء؛ تحديث آخر دخول يبقى في
    مسار /api/admin/login الكامل). يُرجع (admin, tenant_id) عند النجاح
    أو None عند أي فشل (مستخدم غير موجود/معطّل/كلمة مرور خاطئة/بلا tenant).

    ملاحظة: Basic يرسل الاعتماد مع كل طلب — يجب استخدامه فوق HTTPS في
    الإنتاج (نفس اعتبار Bearer). نقبل فقط مخطط basic.
    """
    auth = request.authorization
    if not auth or (getattr(auth, "type", "") or "").lower() != "basic":
        return None
    username = (auth.username or "").strip()
    password = auth.password or ""
    if not username or not password:
        return None
    try:
        from app.radius.db.repos import admins_repo
        admin = admins_repo.get_by_username(username)
        if not admin or not getattr(admin, "enabled", False):
            return None
        if not admins_repo.verify_password(password, admin.password_hash):
            return None
    except Exception:  # noqa: BLE001 — أي خطأ في القراءة/التحقق = رفض (fail closed)
        return None
    tenant_id = _resolve_admin_tenant(admin)
    if tenant_id is None:
        return None
    return admin, tenant_id


def enforce_api_auth():
    """النواة المشتركة للمصادقة: تصادق الطلب الحالي وتضع سياقه في g،
    أو تُرجع رد فشل (401/429). تُرجع None عند **النجاح**.

    تُستدعى من موضعين:
      • الديكوريتر `require_api_token` على نقاط محدّدة — السلوك التاريخي،
        يبقى مفعّلًا دائمًا بصرف النظر عن مفتاح الفرض.
      • الحارس المركزي `install_global_api_auth_guard` (before_request على
        كل مسارات /api) عند تفعيل `api_auth_required()` — فيشمل أي نقطة
        جديدة (مثلاً أضافها تطبيق Flutter على الـVPS) دون لمس توقيعها.

    short-circuit: إن سبق التحقق في هذا الطلب (g._api_authed) لا نكرّره —
    يمنع ازدواج فحص rate limit حين يجتمع الحارس + الديكوريتر على نفس النقطة.
    """
    if getattr(g, "_api_authed", False):
        return None

    token = _extract_bearer()

    tenant_id = 1
    token_id = None
    token_scopes: list[str] = []
    admin_id = 0
    rpm = 60  # default
    rate_key = None  # مُعرّف يُبنى عليه مفتاح rate limit

    if token:
        # ═══ المسار 1: مفتاح/توكن API ═══
        # 1. env token (dev fallback included only when not in production)
        if token in _allowed_env_tokens():
            tenant_id = 1
            token_scopes = ["admin:full"]
        else:
            # 2. DB token
            try:
                from app.radius.db.repos import api_tokens_repo
                rec = api_tokens_repo.resolve_by_plain(token)
            except Exception:  # noqa: BLE001
                rec = None
            if not rec:
                _LOG.warning("invalid api token attempt (len=%d)", len(token))
                return fail("unauthorized", "توكن غير صالح", status=401)
            # 2a. expiry — fail closed
            expired = _is_expired(rec.get("expires_at"))
            if expired is True:
                _LOG.info("expired api token attempted (id=%s)", rec.get("id"))
                return fail(
                    "token_expired",
                    "انتهت صلاحية الـ token — سجّل دخول مجددًا",
                    status=401,
                )
            tenant_id = rec["tenant_id"]
            token_id = rec["id"]
            token_scopes = list(rec.get("scopes") or [])
            admin_id = int(rec.get("created_by") or 0)
            # touch last_used (best-effort)
            try: api_tokens_repo.touch_used(token_id)
            except Exception: pass
            # rate per tenant tier
            try:
                from app.radius.db.repos import tenants_repo
                t = tenants_repo.get_tenant(tenant_id)
                rpm = (t.api_rpm if t else 60) or 60
            except Exception: pass
        rate_key = f"tok:{token_id or token[:12]}"
    else:
        # ═══ المسار 2: اعتماد أدمن (HTTP Basic: يوزر+باس) ═══
        basic = _verify_admin_basic()
        if not basic:
            return fail(
                "unauthorized",
                "مصادقة مطلوبة: مفتاح API (Authorization: Bearer <token> "
                "أو X-API-Key) أو اعتماد أدمن (Basic بيوزر/باس الأدمن).",
                status=401,
            )
        admin, tenant_id = basic
        admin_id = int(admin.id)
        # نمنح صلاحية أدمن كاملة بنفس سلوك /api/admin/login المعتمد.
        token_scopes = ["admin:full"]
        rate_key = f"admin:{admin_id}"

    # rate limit (مشترك بين المسارين)
    rpm = _configured_api_rpm(rpm)
    key_prefix = f"test:{id(current_app)}:" if current_app.testing else ""
    key = f"{key_prefix}{rate_key}"
    if rpm > 0 and not _rate_limit_check(key, per_minute=rpm):
        return fail("rate_limited",
                    f"تجاوزت الحد ({rpm} req/min)", status=429,
                    details={"retry_after_seconds": 60})

    # set context
    g._api_authed = True
    g.api_token = token
    g.api_token_id = token_id
    g.api_token_scopes = token_scopes
    g.admin_id = admin_id
    g.tenant_id = tenant_id
    return None


def require_api_token(view):
    """ديكوريتر يفرض المصادقة على نقطة محدّدة. يبقى فعّالًا دائمًا (لا
    يتأثر بمفتاح الفرض المركزي) — فالنقاط المزخرفة به محميّة في كل الأحوال."""
    @functools.wraps(view)
    def wrapped(*a, **kw):
        err = enforce_api_auth()
        if err is not None:
            return err
        return view(*a, **kw)
    return wrapped


# ═══════════════ الفرض المركزي القابل للتفعيل التدريجي ═══════════════

def api_auth_required() -> bool:
    """هل يُفرض الفرض المركزي للمصادقة على **كل** نقاط /api؟

    الأولوية:
      1. متغيّر البيئة `HOBERADIUS_API_AUTH_REQUIRED` (1/true/on أو 0/off)
         — مناسب للنشر على الـVPS بلا قاعدة بيانات.
      2. وإلا إعداد `security.api_auth_required` على tenant 1 (افتراضي «0»).

    الافتراضي «معطّل» **متعمَّد** لتدرّج آمن: النقاط المزخرفة بالديكوريتر
    تبقى محمية كما هي، أمّا النقاط غير المزخرفة (ربما أضافها تطبيق Flutter
    على الـVPS ولم تُدفَع لهذا الريبو) فتبقى مفتوحة حتى يجهّز المالك
    المفاتيح ويحدّث التطبيقات، ثم يفعّل المفتاح فيُغلق كل شيء دفعةً واحدة.
    """
    raw = (os.environ.get("HOBERADIUS_API_AUTH_REQUIRED") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        from app.radius.db.repos import tenants_repo
        val = (tenants_repo.get_setting(1, "security.api_auth_required", "0")
               or "0").strip().lower()
        return val in {"1", "true", "yes", "on"}
    except Exception:  # noqa: BLE001
        return False


# مسارات تُعفى من الحارس المركزي: لها مصادقتها الخاصة (متجر/داخلي/دخول
# بطاقة) أو عامة بطبيعتها (صحّة/إصدار/وثائق/تسجيل دخول الأدمن). نُطابق
# بالمسار لا باسم الـendpoint كي نشمل أي نقطة VPS جديدة تحت هذه البادئات.
_PUBLIC_API_PREFIXES = ("/api/v1/store/", "/api/v1/internal/")
_PUBLIC_API_PATHS = frozenset({
    "/api", "/api/", "/api/docs", "/api/openapi.json",
    "/api/admin/login",
    "/api/v1/health", "/api/v1/version",
    "/api/v1/hotspot/cards/login",
})


def _is_public_api_path() -> bool:
    if request.method == "OPTIONS":
        return True  # preflight — لا يُحجب أبدًا (CORS)
    p = request.path or ""
    if p in _PUBLIC_API_PATHS:
        return True
    return any(p.startswith(pre) for pre in _PUBLIC_API_PREFIXES)


def install_global_api_auth_guard(bp) -> None:
    """يركّب فرضاً مركزياً للمصادقة على كامل مساحة /api (شاملاً النقاط
    المتداخلة في v1 وأي نقطة مستقبلية) عبر before_app_request مع تقييدٍ
    بالمسار. مركزي تمامًا: لا حاجة لتعديل كل دالة على حِدة.

    • مفتاح معطّل (الافتراضي) → لا يفعل شيئًا (توافق رجعي كامل).
    • مفتاح مفعّل → كل نقطة /api (عدا _is_public_api_path) تتطلب مفتاح API
      صحيح أو اعتماد أدمن، وإلا 401.
    """
    @bp.before_app_request
    def _global_api_auth():  # noqa: ANN202
        p = request.path or ""
        if p != "/api" and not p.startswith("/api/"):
            return None  # ليس مسار API — تجاهل
        if not api_auth_required():
            return None  # الفرض المركزي معطّل — توافق رجعي
        if _is_public_api_path():
            return None
        return enforce_api_auth()
