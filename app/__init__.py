"""
Standalone Flask app لاستضافة وحدة RADIUS بشكل مستقل.

الهدف:
- تطوير ومعاينة وحدة `app/radius/` بدون الاعتماد على HobeHub.
- نفس البنية والـ blueprint والقوالب التي ستُدمج لاحقًا — لا تعديل عند الدمج.

ما يوفّره هذا الـ app غير ما توفّره HobeHub:
- تسجيل الـ radius blueprint مباشرة على `/admin/radius`.
- صفحة جذر / تُوجّه لـ /admin/radius/devices.
- stubs لـ CSRF + auth + arabize حتى تعمل القوالب بدون legacy_parts.
- layout مستقل بسيط بنفس أسماء البلوكات: title, page_title, crumbs, head_extra, content.

عند الدمج في HobeHub: انسخ `app/radius/` كما هو، وأهمِل هذا الملف.
"""
from __future__ import annotations

import os
import secrets

from flask import Flask, redirect, url_for
from markupsafe import Markup, escape


def create_app() -> Flask:
    from .logging_setup import configure as configure_logging
    configure_logging()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    _install_stubs(app)
    _install_i18n(app)
    _install_api_cors(app)
    _install_store_cors(app)
    _install_store_key_guard(app)
    _init_db(app)
    _install_tenant(app)
    _install_npc_live_adapters(app)
    _register_radius(app)
    _register_api(app)
    _register_root(app)
    _install_cli(app)
    _install_mt_health_context(app)
    _seed_demo(app)
    _start_workers(app)
    return app


def _install_mt_health_context(app: Flask) -> None:
    """Inject `mt_health` into every template, sourced from in-memory
    heartbeats. Zero cost per request — no I/O, no DB, no probe."""
    @app.context_processor
    def _mt_health_inject():
        try:
            from .workers.heartbeat import get_info
            # mt_reconciler publishes routers_ok / routers_skipped each tick.
            info = get_info("mt_reconciler") or {}
            skipped = int(info.get("last_routers_skipped") or 0)
            ok      = int(info.get("last_routers_ok") or 0)
            unreachable = skipped > 0
            return {
                "mt_health": {
                    "unreachable":  unreachable,
                    "skipped":      skipped,
                    "ok":           ok,
                    "any_routers":  (skipped + ok) > 0,
                }
            }
        except Exception:  # noqa: BLE001
            return {"mt_health": {"unreachable": False, "skipped": 0,
                                   "ok": 0, "any_routers": False}}


def _install_i18n(app: Flask) -> None:
    """يهيّئ أساس التدويل (Flask-Babel) — منتقي اللغة + متغيّرات الاتجاه/الخط.

    طبقة فوقية بحتة: عند العربية (الافتراضي) لا يتغيّر أي سلوك حالي. أي فشل
    في التهيئة لا يكسر التطبيق — الموقع يبقى عربيًا كما هو."""
    try:
        from app.radius.i18n import init_i18n
        init_i18n(app)
    except Exception:  # noqa: BLE001 — الترجمة طبقة فوقية لا تكسر الإقلاع
        app.logger.exception("i18n init failed — site stays Arabic (safe default)")


def _install_api_cors(app: Flask) -> None:
    """CORS for /api/* — permissive in dev, allow-list in production.

    Behaviour matrix (env = HOBERADIUS_ENV or FLASK_ENV):

    | env       | HOBERADIUS_CORS_ORIGINS    | result                          |
    |-----------|----------------------------|---------------------------------|
    | dev/empty | unset or "*"               | echo any Origin (wildcard)      |
    | dev/empty | "https://a,https://b"      | explicit allow-list             |
    | prod      | unset, empty, or "*"       | no Access-Control-Allow-Origin  |
    | prod      | "https://a,https://b"      | explicit allow-list             |

    Native Flutter clients (Android/iOS/Windows) do not send an Origin
    header, so they are unaffected by CORS regardless of mode. CORS only
    gates browser-based callers — i.e. someone hitting the JSON API from a
    web page in the admin's browser.

    A misconfigured prod deploy (env=production with origins unset) fails
    closed: browsers cannot read API responses. The mobile/desktop apps and
    direct curl/script calls keep working.
    """
    from flask import request as _req

    raw_origins = (os.environ.get("HOBERADIUS_CORS_ORIGINS") or "").strip()
    env = (
        os.environ.get("HOBERADIUS_ENV")
        or os.environ.get("FLASK_ENV")
        or ""
    ).strip().lower()
    is_prod = env in {"prod", "production"}

    if is_prod:
        # Never default to "*"; literal "*" is rejected too.
        if not raw_origins or raw_origins == "*":
            allowed_origins: tuple[str, ...] = ()
            wildcard = False
        else:
            allowed_origins = tuple(
                o.strip() for o in raw_origins.split(",") if o.strip()
            )
            wildcard = False
    else:
        if not raw_origins or raw_origins == "*":
            allowed_origins = ()
            wildcard = True
        else:
            allowed_origins = tuple(
                o.strip() for o in raw_origins.split(",") if o.strip()
            )
            wildcard = False

    def _origin_allowed(origin: str) -> str:
        if wildcard:
            return origin or "*"
        if not origin:
            return ""
        return origin if origin in allowed_origins else ""

    @app.after_request
    def _cors_headers(resp):
        if not _req.path.startswith("/api/"):
            return resp
        # نقاط متجر المايكروتيك /api/v1/store/* لها سياسة CORS مفتوحة
        # خاصة بها (install_store_cors تضع ACAO:*). لا نلمسها هنا حتى لا
        # تطغى سياسة الـAPI العامة (التي تُردّد الأصل أو تفشل مغلقة في
        # الإنتاج) فوق الـ* المطلوب لصفحة المتجر مجهولة الأصل.
        if _req.path.startswith("/api/v1/store/"):
            return resp
        origin = _req.headers.get("Origin", "")
        echoed = _origin_allowed(origin)
        if echoed:
            resp.headers["Access-Control-Allow-Origin"] = echoed
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Request-Id"
            )
            resp.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PATCH, DELETE, OPTIONS"
            )
            resp.headers["Access-Control-Max-Age"] = "3600"
        return resp

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def _cors_preflight(_any):  # noqa: ARG001
        from flask import make_response
        return make_response("", 204)


def _install_store_cors(app: Flask) -> None:
    """CORS مفتوح لنقاط متجر المايكروتيك /api/v1/store/* فقط.

    صفحة المتجر store.html تعمل من أصل الراوتر (IP غير معروف
    مسبقًا)، والمصادقة بتوكن موقّع في الترويسة لا بكوكيز — فالسماح
    لأي أصل هنا آمن ولا يلمس سياسة CORS العامة لبقية الـ API.
    التفاصيل في app/api/v1/store.py::install_store_cors.
    """
    from app.api.v1.store import install_store_cors
    install_store_cors(app)


def _install_store_key_guard(app: Flask) -> None:
    """بوّابة مفتاح التطبيق لنقاط المتجر — ترفض أي طلب لا يحمل مفتاح
    المتجر الصحيح (X-Store-Key) قبل تحقق التوكن وبعد preflight.
    التفاصيل في app/api/v1/store.py::install_store_key_guard."""
    from app.api.v1.store import install_store_key_guard
    install_store_key_guard(app)


def _start_workers(app: Flask) -> None:
    """يُشغّل خيوط الخلفية لمرّة واحدة: webhook + sync + accounting puller."""
    if os.environ.get("HOBERADIUS_NO_WORKER") or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from app.webhooks.queue_worker import start_worker_once
        start_worker_once()
    except Exception:  # noqa: BLE001
        app.logger.exception("webhook worker start failed")
    try:
        from app.workers import (start_accounting_puller,
                                  start_admin_bridge_sync_worker,
                                  start_backup_scheduler_worker,
                                  start_device_fingerprint_worker,
                                  start_dunning_worker,
                                  start_lifecycle_worker,
                                  start_mt_reconciler,
                                  start_stale_session_reaper,
                                  start_sync_worker,
                                  start_temp_speed_expiry)
        start_sync_worker()
        start_accounting_puller()
        start_stale_session_reaper()
        start_device_fingerprint_worker()
        start_lifecycle_worker()
        start_admin_bridge_sync_worker()
        start_mt_reconciler()
        start_backup_scheduler_worker()
        start_dunning_worker()
        start_temp_speed_expiry()
    except Exception:  # noqa: BLE001
        app.logger.exception("workers start failed")
    try:
        from app.workers.setup_wizard_tentative_reclaimer_worker import (
            start_setup_wizard_tentative_reclaimer,
        )
        start_setup_wizard_tentative_reclaimer(flask_app=app)
    except Exception:  # noqa: BLE001
        app.logger.exception(
            "setup_wizard_tentative_reclaimer start failed"
        )
    try:
        from app.workers.setup_wizard_radius_reconciler_worker import (
            start_setup_wizard_radius_reconciler,
        )
        start_setup_wizard_radius_reconciler(flask_app=app)
    except Exception:  # noqa: BLE001
        app.logger.exception(
            "setup_wizard_radius_reconciler start failed"
        )


def _init_db(app: Flask) -> None:
    """يُنفّذ migrations الجديدة + يبذُر الـ defaults (tenant + roles)."""
    from app.radius.db import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    n = run_pending_migrations()
    if n:
        app.logger.info("applied %d migration(s)", n)
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()


def _install_tenant(app: Flask) -> None:
    from app.radius.middleware import install_tenant_resolver
    install_tenant_resolver(app)


def _install_npc_live_adapters(app: Flask) -> None:
    """Opt-in install of the real MikroTik adapters for the
    Network Policy Center. Reads env vars and either wires the
    live executor + state reader, or leaves the Null adapters
    in place (the default — keeps live traffic off until an
    operator explicitly opts in)."""
    try:
        from app.radius.services.npc_live_bootstrap import (
            install_live_adapters_from_env,
        )
        result = install_live_adapters_from_env(
            logger=app.logger,
        )
        if result.get("installed"):
            app.logger.info(
                "NPC live adapters enabled: %s",
                result.get("reason"),
            )
    except Exception:  # noqa: BLE001
        # Never let a bootstrap failure prevent the app from
        # starting — the Null adapters keep the safe default.
        app.logger.exception(
            "NPC live adapter bootstrap failed — "
            "Null adapters retained (safe default)."
        )


def _seed_demo(app: Flask) -> None:
    if os.environ.get("HOBERADIUS_NO_SEED"):
        return
    env = (
        os.environ.get("HOBERADIUS_ENV")
        or os.environ.get("FLASK_ENV")
        or ""
    ).strip().lower()
    prod = env in {"prod", "production"}
    explicit = (os.environ.get("HOBERADIUS_DEMO_SEED") or "").strip().lower()
    if prod and explicit not in {"1", "true", "yes", "on"}:
        return
    try:
        from app.radius.seed import seed_demo_data
        seed_demo_data()
    except Exception:  # noqa: BLE001
        app.logger.exception("seed failed (non-fatal)")


def _install_cli(app: Flask) -> None:
    """Local operator commands.

    These commands are intentionally explicit. They make local/demo data visible
    without requiring unsafe production auto-seeding.
    """
    import click

    @app.cli.command("seed-demo")
    @click.option(
        "--force",
        is_flag=True,
        help="Top up existing demo data as well as empty demo databases.",
    )
    def _seed_demo_command(force: bool = False) -> None:
        from app.radius.seed import seed_demo_data

        summary = seed_demo_data(force=force)
        click.echo("HobeRadius demo data ready:")
        for key in sorted(summary):
            click.echo(f"- {key}: {summary[key]}")


# ─────────────── stubs (تحاكي HobeHub) ───────────────


def _install_stubs(app: Flask) -> None:
    """يحاكي الـ helpers التي تتوقع القوالب وجودها (csrf, arabize, session)."""

    def csrf_token() -> str:
        from flask import session as flask_session
        tok = flask_session.get("_csrf_token")
        if not tok:
            tok = secrets.token_urlsafe(24)
            flask_session["_csrf_token"] = tok
        return tok

    def csrf_token_input() -> str:
        return Markup(
            f'<input type="hidden" name="_csrf_token" value="{escape(csrf_token())}">'
        )

    def _topbar_alerts(limit: int = 6) -> dict:
        """أحدث التنبيهات الذكية المفتوحة لعرضها في قائمة الجرس المنسدلة.

        تُرجع {count, items} حيث كل عنصر: {id, title, severity, last_seen}.
        كسولة وآمنة: تُستدعى من الـ layout فقط، وأي فشل (قاعدة بيانات غير
        جاهزة، جداول ناقصة…) يُرجِع قائمة فارغة بدل كسر الصفحة.
        """
        try:
            from flask import g as _g
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.db.repos import alerts_repo
            tid = int(getattr(_g, "tenant_id", DEFAULT_TENANT_ID))
            rows = alerts_repo.list_open(tid, limit=50)
            items = [{
                "id": int(r["id"]),
                "title": r.get("title_ar") or "",
                "severity": r.get("severity") or "info",
                "last_seen": r.get("last_seen") or "",
            } for r in rows[: max(1, int(limit))]]
            return {"count": len(rows), "items": items}
        except Exception:  # noqa: BLE001 — الجرس لا يكسر أي صفحة أبدًا
            return {"count": 0, "items": []}

    def _sync_pending_count() -> int:
        """عدد مهام المزامنة المنتظرة/المعاد محاولتها (شارة الـ sidebar فقط).

        طابور المزامنة هو عصب التحكم بالراوترات — الشارة بجانب رابط
        «طابور المزامنة» تكشف فورًا وجود مهام لم تصل للراوترات بعد.
        كسولة وآمنة: استعلام عدّ واحد خفيف على فهرس status، وأي خطأ
        يُرجِع صفرًا بدل كسر أي صفحة.
        """
        try:
            from flask import g as _g
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.db.connection import db as _db
            tid = int(getattr(_g, "tenant_id", DEFAULT_TENANT_ID))
            row = _db().execute(
                "SELECT COUNT(*) AS c FROM sync_queue "
                "WHERE tenant_id = ? AND status IN ('queued','retrying','syncing')",
                (tid,),
            ).fetchone()
            return int(row["c"] if row else 0)
        except Exception:  # noqa: BLE001 — الشارة لا تكسر أي صفحة أبدًا
            return 0

    def _collection_is_frozen() -> bool:
        """هل قسم التحصيل مجمّد؟ (شارة الـ sidebar فقط).

        كسولة وآمنة: تُستدعى من الـ sidebar عند رسم رابط «التحصيل
        والمدفوعات». استعلام إعدادات واحد خفيف. عند أي خطأ تعتبر القسم
        مجمّدًا (الوضع الآمن) — انظر سياسة collection_frozen.
        """
        try:
            from flask import g as _g, session as _s
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.routes.finance_collection import collection_frozen_now
            tid = int(
                getattr(_g, "tenant_id", None)
                or _s.get("tenant_id")
                or DEFAULT_TENANT_ID
            )
            return collection_frozen_now(tid)
        except Exception:  # noqa: BLE001 — الشارة لا تكسر أي صفحة أبدًا
            return True

    # ── صلاحيات الواجهة (RBAC UI) — حقن مشروط بطبقة auth/ui_permissions ──
    # المنطق الكامل (can/perm_for_endpoint/ui_unauth_mode + التخزين لكل طلب
    # + خريطة endpoint→صلاحية) يعيش في app/radius/auth/ui_permissions.py.
    #
    # عقد fail-open الحاسم: نحقن can/perm_for_endpoint **فقط** عندما تُستورَد
    # الطبقة بنجاح. إن غاب الملف أو فشل الاستيراد لأي سبب، نُسقط المفتاحين
    # عمدًا — فيقرأ السايدبار `_rbac_ui = (can is defined and
    # perm_for_endpoint is defined)` كـFalse ويرتدّ لـ«عرض الكل» (fail-open).
    #
    # هذا يعالج جذر عطل الإنتاج: الأغلفة القديمة كانت محقونة **دائمًا** لكنها
    # تبتلع ImportError وتُرجِع False لكل مفتاح — فيبقى _rbac_ui=True بينما
    # can() يفشل بصمت ⇒ تجميد كل الأقسام حتى للسوبر. الآن: استيراد فاشل =
    # لا حقن = عرض الكل، بدل التجميد الكارثي.
    def _rbac_ui_context() -> dict:
        try:
            from app.radius.auth import ui_permissions as _uip
        except Exception:  # noqa: BLE001 — الطبقة غائبة/معطوبة → fail-open
            app.logger.exception(
                "RBAC-UI layer unavailable — sidebar fails OPEN "
                "(all sections visible). Check app/radius/auth/ui_permissions.py "
                "is present in the build (was historically eaten by the '????/' "
                ".gitignore rule on the 4-char 'auth/' dir)."
            )
            return {}
        # الدوال نفسها تبتلع أخطاءها الداخلية وتُرجِع قيمًا آمنة، فحقنها مباشرةً
        # آمن — can() يُرجِع True للسوبر دائمًا (يفحص session['is_super_admin']).
        return {
            "can": _uip.can,
            "ui_unauth_mode": _uip.ui_unauth_mode,
            "perm_for_endpoint": _uip.perm_for_endpoint,
        }

    @app.context_processor
    def _inject():
        from flask import session as flask_session
        ctx = {
            "csrf_token": csrf_token,
            "csrf_token_input": csrf_token_input,
            "session": flask_session,
            "admin_page_guide": lambda *a, **k: {"title": "", "steps": [], "tips": [], "links": []},
            # `endpoint_exists` lets sidebar / nav templates check
            # whether an endpoint is registered before calling
            # url_for — protects against BuildError when a new
            # sidebar entry ships a release before its route does,
            # or when a route is retired but the entry is still
            # listed. O(1) lookup; never touches the URL builder.
            "endpoint_exists": lambda name: name in app.view_functions,
            # تنبيهات شريط الأعلى (الجرس): دالة كسولة تُستدعى من الـ layout فقط،
            # تُرجع أحدث التنبيهات المفتوحة للمستأجر الحالي — كل عنصر يحمل id
            # للانتقال لصفحة تفاصيله. لا تكسر الصفحة أبدًا عند أي خطأ.
            "topbar_alerts": _topbar_alerts,
            # تجميد قسم التحصيل: دالة كسولة تستدعيها الـ sidebar فقط لعرض
            # شارة «مجمّد» بجانب رابط التحصيل. استعلام واحد خفيف، ولا تكسر
            # أي صفحة عند الخطأ (تعتبر القسم مجمّدًا افتراضيًا — الوضع الآمن).
            "collection_is_frozen": _collection_is_frozen,
            # شارة طابور المزامنة: دالة كسولة تستدعيها الـ sidebar لعرض عدد
            # المهام المنتظرة بجانب رابط «طابور المزامنة». استعلام عدّ واحد،
            # ولا تكسر أي صفحة عند الخطأ (تُرجع صفرًا).
            "sync_pending_count": _sync_pending_count,
            # is_super_admin: علم صريح يُقرأ من الجلسة مباشرةً — مصدر حقيقة
            # مستقل عن can()/طبقة الـRBAC. الـ sidebar يعتمد عليه ليفتح كل
            # الأقسام للمدير الرئيسي دائمًا (fail-open) حتى لو فشل حقن أو
            # تحليل الصلاحيات بصمت. قراءة قاموس واحدة، لا تكسر أي صفحة.
            "is_super_admin": bool(flask_session.get("is_super_admin")),
        }
        # ── صلاحيات الواجهة (RBAC UI) — حقن مشروط (fail-open عند الغياب) ──
        # can(perm): هل يملك المسؤول الحالي الصلاحية؟ (super_admin دائمًا نعم)
        # ui_unauth_mode(): "freeze" تجميد بقفل أو "hide" إخفاء كلي.
        # perm_for_endpoint(ep): مفتاح الصلاحية لبند sidebar حسب endpoint.
        # تُحقن فقط إن استُورِدت الطبقة؛ وإلا تُترك غير معرّفة → السايدبار
        # يرتدّ لعرض الكل بدل التجميد. راجع _rbac_ui_context أعلاه.
        ctx.update(_rbac_ui_context())
        return ctx

    # Unified system config (currency / timezone / branding) + money & local-time
    # filters — single source of truth read from tenant_settings.
    @app.context_processor
    def _inject_system_config():
        try:
            from app.radius.core.system_config import system_config
            return {"cfg": system_config()}
        except Exception:  # noqa: BLE001 — never break a page on config read
            # اشتقّ عملة الاحتياط من الإعداد الافتراضي الموحّد بدل تثبيت JOD هنا.
            from app.radius.core.system_config import (
                CURRENCY_NAMES, CURRENCY_SYMBOLS, _DEFAULTS,
            )
            cur = (_DEFAULTS.get("billing.currency") or "ILS").upper()
            return {"cfg": {"currency": cur,
                            "currency_symbol": CURRENCY_SYMBOLS.get(cur, cur),
                            "currency_name": CURRENCY_NAMES.get(cur, cur),
                            "tz_offset": 3.0, "system_name": "HobeRadius", "country": "",
                            "logo_url": "", "primary_color": "#2BAACC"}}

    from app.radius.core.system_config import (
        format_duration_days as _dur_days,
        format_money as _fmt_money,
        to_local as _to_local,
        to_local_date as _to_local_date,
    )
    app.jinja_env.filters["money"] = _fmt_money
    app.jinja_env.filters["dt_local"] = _to_local
    app.jinja_env.filters["date_local"] = _to_local_date
    # minutes → friendly Arabic days string ("3 أيام و18 ساعة"). Durations
    # are stored in MINUTES but operators think in DAYS — see SERVICES_COOKBOOK.
    app.jinja_env.filters["dur_days"] = _dur_days

    # No-op arabize filters (HobeHub يحوّلها لأسماء عربية)
    app.jinja_env.filters.setdefault("arabize", lambda s: s)
    app.jinja_env.filters.setdefault("arabize_audit", lambda s: s)
    app.jinja_env.globals.setdefault(
        "endpoint_exists", lambda name: name in app.view_functions)

    # endpoints مستثناة من CSRF (بوّابات دخول مع credentials check)
    _CSRF_EXEMPT_PATHS = {
        "/admin/radius/login",   # login بوّابة بحد ذاتها + cookie قد لا يكون موجودًا
        "/admin/radius/logout",
        # designer-svg: same-origin live SVG preview endpoint posted to
        # on every form keystroke. Read-only render — never mutates DB.
        # Without the exemption every keystroke would 302 to login.
        "/admin/radius/print-templates/designer-svg",
        # WhatsApp bot inbound webhook (Phase 2): called server-to-server by
        # the WhatsApp gateway, which has no session/CSRF token. It only reads
        # the message and replies via the configured provider — never mutates
        # admin data — and always answers 200.
        "/admin/radius/communications/bot/webhook",
    }

    # CSRF عام مبسَّط — تحقّق الـ token على غير-GET
    @app.before_request
    def _csrf_check():
        from flask import request, session as flask_session
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        # API routes تستخدم Bearer token بدل CSRF
        if request.path.startswith("/api/"):
            return None
        # المسارات المستثناة (login/logout)
        if request.path in _CSRF_EXEMPT_PATHS:
            return None
        sent = (
            request.form.get("_csrf_token")
            or request.headers.get("X-CSRFToken")
            or ""
        )
        expected = flask_session.get("_csrf_token") or ""
        # لو لا يوجد token في الـ session (جلسة جديدة/كوكي قديم) — نولّد ثم نعيد التوجيه بدل الفشل
        if not expected:
            from flask import redirect
            csrf_token()  # يولّد ويحفظ في session
            return redirect(request.referrer or "/admin/radius/login")
        if sent != expected:
            return ("انتهت صلاحية نموذج الحماية. حدّث الصفحة وحاول مرة أخرى", 400)
        return None

    # حقن _csrf_token في كل <form method="post"> تلقائيًا
    import re
    _FORM_RE = re.compile(
        r"(<form\b(?=[^>]*\bmethod\s*=\s*['\"]?post['\"]?)[^>]*>)",
        re.IGNORECASE,
    )

    @app.after_request
    def _inject_csrf(response):
        if response.mimetype != "text/html" or response.direct_passthrough:
            return response
        try:
            html = response.get_data(as_text=True)
        except (RuntimeError, UnicodeDecodeError):
            return response
        if "<form" not in html:
            return response
        token = str(escape(csrf_token()))
        field = f'<input type="hidden" name="_csrf_token" value="{token}">'
        response.set_data(_FORM_RE.sub(r"\1" + field, html))
        return response

    # Network Device Monitor — Sprint 2 background worker.
    # GATED OFF by default (2026-05-28) because the whole Network
    # Operations family is hidden until next release. The sidebar
    # entries are commented out, but the routes stay registered;
    # this gate stops the worker from polling routers in the
    # background and burning CPU/Telegram quota while no one is
    # actually using the feature.
    #
    # To re-enable: set HOBERADIUS_NETWORK_OPS_ENABLED=1 in the
    # environment + uncomment the sidebar entries in
    # app/templates/admin/_sidebar.html.
    import os as _os
    _net_ops_on = (_os.environ.get("HOBERADIUS_NETWORK_OPS_ENABLED") or "").strip().lower() \
                  in ("1", "true", "yes", "on")
    if _net_ops_on and not _os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from app.radius.services import network_device_monitor
            network_device_monitor.start(app)
        except Exception:  # noqa: BLE001
            app.logger.exception(
                "[net-monitor] start failed — alerts inactive")


# ─────────────── radius blueprint ───────────────


def _register_radius(app: Flask) -> None:
    from app.radius.routes import get_radius_blueprint
    app.register_blueprint(get_radius_blueprint())
    # Customer-facing portal at the URL root (no /admin/radius prefix).
    # Reuses the same view functions so behaviour stays in sync; the
    # admin-prefixed routes remain registered for backward-compat with
    # any links / bookmarks already in the wild.
    from app.radius.routes.customer_portals import build_customer_portal_root_blueprint
    app.register_blueprint(build_customer_portal_root_blueprint())


def _register_api(app: Flask) -> None:
    from app.api import get_api_blueprint
    app.register_blueprint(get_api_blueprint())


# ─────────────── root ───────────────


def _register_root(app: Flask) -> None:
    @app.get("/")
    def _root():
        return redirect(url_for("radius.dashboard"))
