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

from flask import Flask, redirect, render_template, url_for
from markupsafe import Markup, escape


def create_app() -> Flask:
    from .logging_setup import configure as configure_logging
    configure_logging()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    _WEAK_SECRETS = {"", "dev-secret-change-me", "change-this-secret",
                     "replace-with-a-long-random-secret-at-least-32-bytes"}
    _flask_secret = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
    _env = (os.environ.get("HOBERADIUS_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
    _is_prod_boot = _env in {"prod", "production"}
    # SEC H5 — the app secret both signs sessions AND is the root from which
    # at-rest encryption keys are derived. Refuse to boot in production on the
    # shipped default (fail-closed, mirrors the admin panel); warn otherwise so
    # the exposure is visible in dev.
    if _flask_secret in _WEAK_SECRETS:
        if _is_prod_boot:
            raise RuntimeError(
                "Production (HOBERADIUS_ENV=production) requires a strong "
                "FLASK_SECRET — the shipped default is not allowed.")
        import logging as _logging
        _logging.getLogger("app").warning(
            "FLASK_SECRET is the insecure default — set a strong random value; "
            "sessions are forgeable and at-rest keys are derivable.")
    app.secret_key = _flask_secret
    # SEC H5 — session-cookie hardening. HttpOnly + SameSite=Lax are pure wins
    # (no downside on HTTP); Secure follows env (default ON in production, and
    # opt-in via HOBERADIUS_SESSION_COOKIE_SECURE elsewhere so an HTTPS-fronted
    # deployment can enable it without a code change). Do NOT force Secure on a
    # plain-HTTP panel — the cookie would never be sent and login would break.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("HOBERADIUS_SESSION_COOKIE_SAMESITE", "Lax")
    _secure_default = "1" if _is_prod_boot else "0"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("HOBERADIUS_SESSION_COOKIE_SECURE", _secure_default)
        .strip().lower() in {"1", "true", "yes", "on"})
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    # سقف حجم جسم الطلب = 500MB (رفع تفريغات قاعدة كبيرة لمعالج الترحيل).
    # كان None (بلا حدّ على مستوى التطبيق؛ nginx وحده يَحدّ). werkzeug يرفع 413
    # عند التجاوز — نُحوّله لـJSON للمسارات التي تتوقّعه (لا صفحة HTML تُسقط
    # الواجهة). القيمة قابلة للضبط عبر HOBERADIUS_MAX_UPLOAD_MB.
    try:
        _max_mb = int(os.environ.get("HOBERADIUS_MAX_UPLOAD_MB", "500"))
    except ValueError:
        _max_mb = 500
    app.config["MAX_CONTENT_LENGTH"] = _max_mb * 1024 * 1024
    # Werkzeug ≥3.1 أضاف حدًّا منفصلًا لحجم حقل النموذج النصّي الواحد
    # (افتراضيه 500KB فقط) — كان يرفض معاينة مصمّم البطاقات بـ413 لأن
    # حقل data URL للخلفية يتجاوزه بسهولة. نرفعه بما يغطي صورة 8MB
    # ملفًّا (~11MB base64) وسقف MAX_CONTENT_LENGTH يبقى الحاكم الكلي.
    app.config["MAX_FORM_MEMORY_SIZE"] = 32 * 1024 * 1024

    from werkzeug.exceptions import RequestEntityTooLarge

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_request_too_large(exc):  # noqa: ANN001
        from flask import request as _rq, jsonify as _jsonify
        wants_json = (
            _rq.path.startswith("/admin/radius/migrate")
            or _rq.path.startswith("/api/")
            or _rq.headers.get("X-CSRFToken") is not None
            or "application/json" in (_rq.headers.get("Accept") or "")
        )
        if wants_json:
            return _jsonify({
                "ok": False,
                "status": "too_large",
                "error": (f"الملفّ أكبر من الحدّ المسموح ({_max_mb}MB). ارفع "
                          "النسخة المضغوطة .gz (أصغر بكثير) أو تفريغًا أصغر."),
            }), 413
        return exc  # صفحة HTML الافتراضيّة لبقيّة المسارات.

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
    _install_captive_redirect(app)
    _install_cli(app)
    _install_mt_health_context(app)
    _seed_demo(app)
    # يُشغَّل بعد بذر التجريبيّة عمدًا: في وضع التجربة تكون البيانات (والمدير
    # التجريبيّ admin/admin) قد بُذرت فلا يفعل شيئًا؛ وفي الإنتاج النظيف
    # (بلا بذر) لا يوجد مدير فيُنشئ الافتراضيّ admin/123456789 — دخول مضمون
    # لأيّ نسخة/VPS جديدة، مستقلًّا عن البذر ومُمتنع التكرار.
    try:
        from app.radius.db.repos import admins_repo
        admins_repo.ensure_bootstrap_admin()
    except Exception:  # noqa: BLE001 — must never break startup
        app.logger.warning("bootstrap admin failed (non-fatal)", exc_info=True)
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
                                  start_bandwidth_schedule_worker,
                                  start_device_fingerprint_worker,
                                  start_device_health_poll_worker,
                                  start_dunning_worker,
                                  start_lifecycle_worker,
                                  start_log_retention_worker,
                                  start_loop_probe_poller,
                                  start_mt_reconciler,
                                  start_schedule_window_worker,
                                  start_self_update_worker,
                                  start_speed_split_worker,
                                  start_stale_session_reaper,
                                  start_store_chat_reminder_worker,
                                  start_sync_worker,
                                  start_temp_speed_expiry)
        from app.workers.remote_access_reaper_worker import (
            start_remote_access_reaper)
    except Exception:  # noqa: BLE001 — تعذّر الاستيراد ⇒ لا خيوط، لكن لا نُسقِط الإقلاع
        app.logger.exception("workers import failed")
        return

    # عزل كل خيط: فشل بدء worker واحد (config/شبكة/تبعيّة على خادم الإنتاج) كان
    # يقفز إلى except الواحد الجامع فيمنع كلّ ما بعده — وعلى الأخصّ
    # device_health_poll_worker (مراقبة انقطاع الراوترات: ccr3) الذي كان يأتي
    # متأخّرًا في التسلسل. هذا فسّر «التشخيص عند الطلب يكشف الانقطاع لكنّ المراقب
    # الخلفي لا يُحدّث الحالة ولا يُنبّه». الآن كلٌّ مستقلّ، والمراقبة تبدأ أوّلًا.
    def _safe_start(label: str, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — فشل خيط لا يمنع البقية ولا يكسر الإقلاع
            app.logger.exception("worker start failed: %s", label)

    # ── أولوية تشغيلية: مراقبة انقطاع/عودة الراوترات + كشف اللوب تبدأ أوّلًا ──
    # السوبر/الإدارة تتحكّم بالتفعيل/الدقائق من واجهة device-health، والـworker
    # يقرأها لكل مستأجر كل tick (60s). poll_enabled الافتراضي مفعّل.
    _safe_start("device_health_poll", start_device_health_poll_worker)
    _safe_start("loop_probe_poller", start_loop_probe_poller)
    _safe_start("sync", start_sync_worker)
    _safe_start("accounting_puller", start_accounting_puller)
    _safe_start("stale_session_reaper", start_stale_session_reaper)
    _safe_start("device_fingerprint", start_device_fingerprint_worker)
    _safe_start("lifecycle", start_lifecycle_worker)
    _safe_start("admin_bridge_sync", start_admin_bridge_sync_worker)
    _safe_start("mt_reconciler", start_mt_reconciler)
    # Periodic "is a newer version available?" check (opt-in self-update).
    # Degrades silently when the license bridge is off/unreachable.
    _safe_start("self_update_check", start_self_update_worker, app)
    _safe_start("backup_scheduler", start_backup_scheduler_worker)
    _safe_start("log_retention", start_log_retention_worker)
    _safe_start("dunning", start_dunning_worker)
    _safe_start("temp_speed_expiry", start_temp_speed_expiry)
    _safe_start("bandwidth_schedule", start_bandwidth_schedule_worker)
    # Enforce connection-schedule / allowed-hours windows on ALREADY-ACTIVE
    # sessions: CoA-disconnect any live session whose window has closed (the
    # authorize-time Session-Timeout is the primary cutoff; this is the backstop).
    _safe_start("schedule_window", start_schedule_window_worker)
    # إعادة توزيع «تقسيم السرعة على الأجهزة» عند تغيّر عدد الجلسات المفتوحة —
    # FreeRADIUS يكتب radacct عبر SQL مباشرةً فلا خطّاف محاسبة يصلنا (انظر
    # speed_split_worker docstring).
    _safe_start("speed_split", start_speed_split_worker)
    _safe_start("remote_access_reaper", start_remote_access_reaper)
    _safe_start("store_chat_reminder", start_store_chat_reminder_worker, app)
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
    from app.radius.db.repos import access_blocks_repo, admins_repo, tenants_repo
    n = run_pending_migrations()
    if n:
        app.logger.info("applied %d migration(s)", n)
    # schema self-heal: العمود access_blocks.layer أُضيف لـmigration 123 في
    # منتصف الفرع؛ قاعدة طبّقت 123 قبل ذلك لا تُعاد تشغيلها (تتبّع بالاسم)
    # فتُشفى هنا بإضافة العمود إن غاب (ممتنع التكرار).
    access_blocks_repo.ensure_schema()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()

    # One-shot idempotent backfill: every v6 SSTP/PPTP router gets an
    # MSCHAP-ready rtr- RADIUS account automatically — the permanent code path
    # that replaces the manual `rtr-ccr4` SQL insert. Complete accounts are left
    # untouched (no password churn); missing/incompatible ones are provisioned,
    # and the new secret is then revealable from the credential-management UI.
    try:
        from app.radius.services import router_mgmt_tunnel as _rmt
        from app.radius.core.tenant import DEFAULT_TENANT_ID as _DTID
        rep = _rmt.reconcile_tunnel_accounts(_DTID)
        if rep.changed:
            app.logger.info(
                "mgmt-tunnel reconcile: %d account(s) provisioned/repaired",
                rep.changed,
            )
        # Abuse prevention: apply the current rate cap (Filter-Id) to every
        # existing rtr-* account so the SSTP/PPTP bandwidth limit is enforced
        # on already-provisioned routers too (not just freshly added ones).
        capped = _rmt.reconcile_rate_caps(tenant_id=_DTID)
        if capped:
            app.logger.info("mgmt-tunnel rate cap: applied to %d rtr-* account(s)",
                            capped)
    except Exception:  # noqa: BLE001 — never block boot on reconcile
        app.logger.exception("mgmt-tunnel reconcile failed (non-fatal)")


def _install_tenant(app: Flask) -> None:
    from app.radius.middleware import install_tenant_resolver
    install_tenant_resolver(app)
    # MT22 — توجيه المسار باسم الشبكة (path-based tenancy). طبقة WSGI تُركّب
    # التطبيق تحت /<slug> فتحمل url_for بادئة الشبكة تلقائيًّا. مع كوكي جلسة
    # على '/' كي تعمل الجلسة عبر كل بادئات الشبكات وصفحة المزوّد بلا slug.
    app.config["SESSION_COOKIE_PATH"] = "/"
    from app.radius.middleware.tenant_path import TenantPathMiddleware
    app.wsgi_app = TenantPathMiddleware(app.wsgi_app)


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

    @app.cli.command("check-update")
    def _check_update_command() -> None:
        """Run one self-update check now (parity with `flask sync-license`)."""
        from app.radius.services import self_update

        state = self_update.check_for_update(1)
        click.echo(f"running : {state.get('current')}")
        click.echo(f"latest  : {state.get('latest') or '(unknown)'}")
        click.echo(f"available: {bool(state.get('available'))}")
        if state.get("blocked_direct_jump"):
            click.echo(
                f"NOTE: below min_version {state.get('min_version')} — "
                f"intermediate step required (target {state.get('target_version')})"
            )
        click.echo(f"reason  : {state.get('reason')}")


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

    def _topbar_notifications(limit: int = 6) -> dict:
        """مركز الإشعارات الموحّد لجرس شريط الأعلى (الظرف): أحدث الإشعارات
        + عدّ غير المقروء للمستأجر الحالي. كسولة وآمنة — تُستدعى من الـ layout
        فقط، وأي فشل يُرجِع {0, []} بدل كسر الصفحة.

        تُرجع {count, items} حيث كل عنصر يحمل id/type/severity/title/link/
        created_at/is_read للانتقال المباشر لهدف الإشعار."""
        try:
            from flask import g as _g
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.services import notifications as _notif
            tid = int(getattr(_g, "tenant_id", DEFAULT_TENANT_ID))
            return {
                "count": _notif.unread_count(tid),
                "items": _notif.recent_for_bell(tid, limit=max(1, int(limit))),
            }
        except Exception:  # noqa: BLE001 — جرس الإشعارات لا يكسر أي صفحة أبدًا
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

    def _mt_active_remote_count() -> int:
        """عدد جلسات الوصول البعيد (WinBox) المفتوحة الآن للمستأجر — يُشغّل
        المؤشّر الأخضر على تبويب «الوصول البعيد». كسولة (تُستدعى من شريط
        تنقّل الشبكة فقط) وآمنة: أي خطأ يُرجِع صفرًا."""
        try:
            from flask import g as _g
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.services import router_remote_access as _ra
            tid = int(getattr(_g, "tenant_id", DEFAULT_TENANT_ID))
            return int(_ra.active_session_count(tid))
        except Exception:  # noqa: BLE001 — المؤشّر لا يكسر أي صفحة أبدًا
            return 0

    def _update_badge() -> dict:
        """Topbar self-update indicator — {available, latest, blocked}.

        Only surfaces for the owner/super (the update flow is owner-gated).
        Lazy + safe: called from the layout only; any failure → not-available
        so the icon simply stays quiet. Never breaks a page.
        """
        try:
            from flask import g as _g, session as _s
            if not bool(_s.get("is_super_admin")):
                return {"available": False, "latest": "", "blocked": False}
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.services import self_update as _su
            tid = int(getattr(_g, "tenant_id", None) or _s.get("tenant_id") or DEFAULT_TENANT_ID)
            state = _su.get_cached_state(tid)
            return {
                "available": bool(state.get("available")),
                "latest": str(state.get("latest") or ""),
                "blocked": bool(state.get("blocked_direct_jump")),
            }
        except Exception:  # noqa: BLE001 — the update dot never breaks a page
            return {"available": False, "latest": "", "blocked": False}

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

    def _license_days(tenant_id: int | None = None) -> dict:
        """أيّام الترخيص المتبقّية لشارة شريط الأعلى (للقراءة فقط، لا آثار
        جانبيّة — لا تُنشئ إشعارًا). تُرجع {days_left, color, pulse, expiry}
        أو {days_left: None} حين لا بيانات ترخيص. كسولة وآمنة — لا تكسر أي
        صفحة أبدًا.

        الألوان (طلب المالك): ≥20 يوم أخضر · 10–19 أصفر · 3–9 أحمر ·
        <3 أحمر نابض (يشمل المنتهي)."""
        try:
            from flask import g as _g
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.services.license_lifecycle import evaluate_cached
            from app.radius.services.notifications import license_days_badge
            tid = int(tenant_id if tenant_id is not None
                      else getattr(_g, "tenant_id", DEFAULT_TENANT_ID))
            decision = evaluate_cached(tid)
            return license_days_badge(getattr(decision, "expires_at", None))
        except Exception:  # noqa: BLE001 — شارة الترخيص لا تكسر أي صفحة
            return {"days_left": None}

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
            # مركز الإشعارات الموحّد (الظرف في شريط الأعلى): دالة كسولة تُرجع
            # أحدث الإشعارات + عدّ غير المقروء. كل عنصر يحمل link للانتقال
            # المباشر لهدفه. لا تكسر الصفحة أبدًا عند أي خطأ.
            "topbar_notifications": _topbar_notifications,
            # مؤشّر تحديث النظام (أعلى الشريط): {available, latest, blocked}.
            # يظهر للمالك/السوبر فقط، كسول وآمن — لا يكسر أي صفحة.
            "update_badge": _update_badge,
            # أيّام الترخيص المتبقّية لشارة شريط الأعلى (للقراءة فقط، بألوان).
            "license_days": _license_days,
            # تجميد قسم التحصيل: دالة كسولة تستدعيها الـ sidebar فقط لعرض
            # شارة «مجمّد» بجانب رابط التحصيل. استعلام واحد خفيف، ولا تكسر
            # أي صفحة عند الخطأ (تعتبر القسم مجمّدًا افتراضيًا — الوضع الآمن).
            "collection_is_frozen": _collection_is_frozen,
            # شارة طابور المزامنة: دالة كسولة تستدعيها الـ sidebar لعرض عدد
            # المهام المنتظرة بجانب رابط «طابور المزامنة». استعلام عدّ واحد،
            # ولا تكسر أي صفحة عند الخطأ (تُرجع صفرًا).
            "sync_pending_count": _sync_pending_count,
            # المؤشّر الأخضر على تبويب «الوصول البعيد»: عدد جلسات WinBox
            # المفتوحة الآن. كسول — يستدعيه network_ops_nav فقط.
            "mt_active_remote_count": _mt_active_remote_count,
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

    # Granular per-manager grants — sidebar/template helpers. The owner sets a
    # 3-state (open/locked/hidden) per section for each manager; the SERVER
    # route guard (routes/blueprint._perm_guard) is the real boundary, these
    # helpers only mirror it in the UI:
    #   • manager_nav_hidden(ep)   → hide the item/section entirely (hidden).
    #   • manager_section_locked(sec_or_ep) → section is view-only (locked):
    #       templates hide/disable mutating controls with this.
    #   • manager_can_write(sec_or_ep) → convenience inverse (open => True).
    # Super/primary owner is never restricted (short-circuit on the flag).
    @app.context_processor
    def _inject_manager_grants():
        from flask import session as _sess

        def _is_super() -> bool:
            try:
                return bool(_sess.get("is_super_admin"))
            except Exception:  # noqa: BLE001
                return False

        def _state_for(sec_or_ep: str) -> str:
            try:
                from app.radius.services import manager_grants as _mg
                aid = _sess.get("admin_id")
                tid = int(_sess.get("tenant_id") or 1)
                if sec_or_ep in _mg.MANAGER_SECTION_REGISTRY:
                    return _mg.section_state(aid, sec_or_ep, tenant_id=tid)
                return _mg.endpoint_state(aid, sec_or_ep, tenant_id=tid)
            except Exception:  # noqa: BLE001 — fail-open (open)
                return "open"

        def _manager_nav_hidden(endpoint: str) -> bool:
            # مخفيّ صراحةً أو «فارغ فعليًّا» (لا عرض/فعل/حقل) → يُزال من السايدبار.
            if _is_super():
                return False
            try:
                from app.radius.services import manager_grants as _mg
                aid = _sess.get("admin_id")
                tid = int(_sess.get("tenant_id") or 1)
                perms = _sess.get("permissions") or []
                return _mg.endpoint_effectively_hidden(
                    aid, endpoint, tenant_id=tid, perms=perms)
            except Exception:  # noqa: BLE001 — fail-open (visible)
                return False

        def _manager_section_locked(sec_or_ep: str) -> bool:
            if _is_super():
                return False
            return _state_for(sec_or_ep) == "locked"

        def _manager_can_write(sec_or_ep: str) -> bool:
            if _is_super():
                return True
            return _state_for(sec_or_ep) == "open"

        def _manager_action_allowed(action_key: str) -> bool:
            """هل يُسمح للمدير الحالي بهذا الفعل؟ (لإخفاء الأزرار). السوبر دائمًا
            نعم. الخادم هو الحَكَم النهائيّ (بوّابة _perm_guard) — هذا للعرض."""
            if _is_super():
                return True
            try:
                from app.radius.services import manager_grants as _mg
                aid = _sess.get("admin_id")
                tid = int(_sess.get("tenant_id") or 1)
                return _mg.action_permitted(aid, action_key, tenant_id=tid)
            except Exception:  # noqa: BLE001 — fail-open (لا نَكسر الصفحة)
                return True

        def _manager_can_see(key: str) -> bool:
            """هل يُسمح للمدير الحالي برؤية بيانات حسّاسة (رصيد/أرباح/…)؟
            السوبر دائمًا نعم. يُستخدَم في القوالب لحجب القيمة من الاستجابة."""
            if _is_super():
                return True
            try:
                from app.radius.services import manager_grants as _mg
                aid = _sess.get("admin_id")
                tid = int(_sess.get("tenant_id") or 1)
                return _mg.can_see(aid, key, tenant_id=tid)
            except Exception:  # noqa: BLE001 — fail-open (visible)
                return True

        def _manager_field_locked(entity: str, key: str) -> bool:
            """حقلٌ مقفول على المدير الحالي (التحكّم مُفعَّل + غير ممنوح) →
            القالب يَعرضه للقراءة/معطَّلًا. السوبر لا يُقفَل عليه شيء."""
            if _is_super():
                return False
            try:
                from app.radius.services import manager_grants as _mg
                aid = _sess.get("admin_id")
                tid = int(_sess.get("tenant_id") or 1)
                return _mg.field_locked(aid, entity, key, tenant_id=tid)
            except Exception:  # noqa: BLE001 — fail-open (not locked)
                return False

        return {
            "manager_nav_hidden": _manager_nav_hidden,
            "manager_section_locked": _manager_section_locked,
            "manager_can_write": _manager_can_write,
            "manager_field_locked": _manager_field_locked,
            "manager_action_allowed": _manager_action_allowed,
            "manager_can_see": _manager_can_see,
        }

    # Provider gate template helpers — provider_endpoint_blocked /
    # provider_service_disabled. Used by the sidebar macro to silently hide
    # provider-disabled items (no super-admin override). Memoized per request
    # inside provider_grant.get_payload via flask.g.
    @app.context_processor
    def _inject_provider_gate():
        try:
            from app.radius.auth.provider_gate import template_helpers
            return template_helpers()
        except Exception:  # noqa: BLE001 — never break a page on helper import
            return {
                "provider_endpoint_blocked": lambda _ep: False,
                "provider_service_disabled": lambda _k: False,
            }

    # Current Gregorian year — used by server-rendered year dropdowns (e.g.
    # the subscriber expiry picker) so they don't depend on the browser locale.
    @app.context_processor
    def _inject_current_year():
        from datetime import datetime as _dt
        return {"hb_current_year": _dt.utcnow().year}

    # MT52 — شكل الصفحات المشتركة (سلة المحذوفات/الأرشفة) بحسب السياق:
    # لوحة المزوّد للمالك على الجذر في وضع الاستضافة، والشكل التشغيليّ
    # لمدراء الشبكات تحت /<slug>/. يُستهلَك عبر `{% extends _shell_layout %}`.
    @app.context_processor
    def _inject_shell_layout():
        try:
            from flask import request, session
            from app.radius.core.hosting_mode import open_hosting
            provider_ctx = (
                open_hosting()
                and bool(session.get("is_super_admin"))
                and not request.environ.get("hoberadius.tenant_slug"))
            return {"_shell_layout": ("admin/_provider_layout.html"
                                      if provider_ctx else "admin/_admin_layout.html")}
        except Exception:  # noqa: BLE001 — لا نكسر صفحةً على فحص تجميليّ
            return {"_shell_layout": "admin/_admin_layout.html"}

    # MT50 — قائمة العملات المعرّفة (كود → اسم) لقوائم الاختيار المنسدلة.
    @app.context_processor
    def _inject_currencies():
        try:
            from app.radius.core.system_config import CURRENCY_NAMES
            return {"currencies": CURRENCY_NAMES}
        except Exception:  # noqa: BLE001
            return {"currencies": {"JOD": "دينار أردني", "USD": "دولار"}}

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
            cur = (_DEFAULTS.get("billing.currency") or "JOD").upper()
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

    # «وقت البطاقة» — human-friendly Arabic base-time label for a card's total
    # time budget. Whole units read as Arabic words («3 ساعات»); mixed budgets
    # return the shared bidi-safe Latin abbreviation. Single source of truth in
    # core.duration_fmt so the checker template never re-implements it.
    #
    # Imported DEFENSIVELY: this runs inside create_app(), so a top-level import
    # failure here (missing/broken module) aborts the whole app boot → EVERY
    # admin page 500s, not just the ones that use this formatter. That is a
    # panel-wide single point of failure. On any import error we fall back to a
    # minimal, self-contained (text, is_latin) formatter so pages keep rendering
    # (a degraded Latin label instead of a dead panel).
    try:
        from .radius.core.duration_fmt import fmt_base_time_ar as _fmt_base_time_ar
    except Exception:  # noqa: BLE001 — never let a formatter import brick the panel
        app.logger.exception("fmt_base_time_ar import failed; using degraded fallback")

        def _fmt_base_time_ar(seconds):
            s = max(0, int(seconds or 0))
            if s <= 0:
                return "", False
            d, rem = divmod(s, 86400)
            h, rem = divmod(rem, 3600)
            m, _sec = divmod(rem, 60)
            parts = []
            if d:
                parts.append(f"{d}d")
            if h:
                parts.append(f"{h}h")
            if m:
                parts.append(f"{m}m")
            return (" ".join(parts) or f"{s}s"), True
    app.jinja_env.globals.setdefault("fmt_base_time_ar", _fmt_base_time_ar)

    # No-op arabize filters (HobeHub يحوّلها لأسماء عربية)
    app.jinja_env.filters.setdefault("arabize", lambda s: s)
    app.jinja_env.filters.setdefault("arabize_audit", lambda s: s)
    app.jinja_env.globals.setdefault(
        "endpoint_exists", lambda name: name in app.view_functions)

    # الإصدار الحقيقيّ للنسخة العاملة — تعرضه ذيليّة اللوحة ليتأكّد العميل بصريًّا
    # بعد تثبيت تحديث أنّ الكود الجديد قد هبط فعلًا. مصدر موحّد في core/app_version.
    from .radius.core.app_version import running_version as _running_version
    app.jinja_env.globals.setdefault("running_version", _running_version)

    # تعريب مفاتيح صلاحيات المشغّلين (can_* → عربي) — مصدر موحّد يُستخدَم في
    # قالب ملف المشغّل وأيّ واجهة صلاحيات شقيقة. انظر services/permission_labels.
    from .radius.services.permission_labels import permission_label as _perm_label
    app.jinja_env.globals.setdefault("permission_label", _perm_label)
    app.jinja_env.filters.setdefault("permission_label", _perm_label)

    # رقم الراوتر المعروض «#N» = ترتيبه بين راوترات المستأجر الحيّة، لا
    # المعرّف الداخليّ (AUTOINCREMENT لا يُعاد — تجارب محذوفة كانت تجعل
    # الراوتر الوحيد يظهر «#39»). المعرّف الداخليّ يبقى في الروابط (مفتاح
    # تقنيّ للحسابات rtr-<id> والملفّات) — العرض فقط ترتيبيّ.
    from .radius.db.repos.nas_repo import display_ordinal as _router_no
    app.jinja_env.globals.setdefault("router_no", _router_no)

    # MT38 — مُنسّقان للعرض. كانت اللوحة تُظهر «223367» حجمًا و
    # «2026-07-21T19:21» تاريخًا: قيمٌ خام يقرأها الحاسوب لا الإنسان.
    def _hsize(value) -> str:
        try:
            n = float(value or 0)
        except (TypeError, ValueError):
            return "—"
        if n <= 0:
            return "—"
        for unit in ("بايت", "ك.ب", "م.ب", "ج.ب", "ت.ب"):
            if n < 1024 or unit == "ت.ب":
                return (f"{n:.0f} {unit}" if unit == "بايت" else f"{n:.1f} {unit}")
            n /= 1024
        return "—"

    def _hdate(value) -> str:
        """ISO → «YYYY-MM-DD HH:MM». يُعيد النصّ كما هو إن تعذّر التحليل
        (لا نَبتلع قيمةً لا نَفهمها فتَختفي من الشاشة)."""
        from datetime import datetime as _dt
        if not value:
            return "—"
        if isinstance(value, _dt):
            return value.strftime("%Y-%m-%d %H:%M")
        raw = str(value).strip()
        try:
            return _dt.fromisoformat(raw.replace("Z", "")[:19]).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return raw

    app.jinja_env.filters.setdefault("hsize", _hsize)
    app.jinja_env.filters.setdefault("hdate", _hdate)

    # MT41 — تسمية أفعال السجلّ بالعربية (خريطة موسّعة مشتركة). احتياطٌ
    # يُعيد المفتاح الخام إن غاب، فلا يَختفي حدثٌ لا تسمية له.
    def _audit_label(action):
        try:
            from app.radius.services import audit_format as _af
            return _af.action_label(action)
        except Exception:  # noqa: BLE001
            return str(action or "")
    app.jinja_env.globals.setdefault("audit_action_label", _audit_label)

    # endpoints مستثناة من CSRF (بوّابات دخول مع credentials check)
    _CSRF_EXEMPT_PATHS = {
        "/admin/radius/login",   # login بوّابة بحد ذاتها + cookie قد لا يكون موجودًا
        "/admin/radius/logout",
        # MT37 — الرابط السرّي لدخول المالك: نفس صفحة الدخول بمسارٍ آخر،
        # فيَلزمه الإعفاء نفسه وإلّا فشل الإرسال (النموذج بلا توكن).
        *( [admin_login_secret_path()] if admin_login_secret_path() else [] ),
        # designer-svg: same-origin live SVG preview endpoint posted to
        # on every form keystroke. Read-only render — never mutates DB.
        # Without the exemption every keystroke would 302 to login.
        "/admin/radius/print-templates/designer-svg",
        # WhatsApp bot inbound webhook (Phase 2): called server-to-server by
        # the WhatsApp gateway, which has no session/CSRF token. It only reads
        # the message and replies via the configured provider — never mutates
        # admin data — and always answers 200.
        "/admin/radius/communications/bot/webhook",
        # تحليلات صفحة الدخول: navigator.sendBeacon من أجهزة الزبائن
        # بلا جلسة/توكن CSRF. fail-open (204)، لا يمسّ بيانات إدارية.
        "/admin/radius/hotspot-analytics/collect",
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
            # Return JSON for AJAX/JSON requests so fetch().then(r.json()) works
            # and the UI shows a readable message instead of swallowing the error.
            if request.is_json or request.headers.get("X-CSRFToken") is not None:
                from flask import jsonify as _jsonify
                return _jsonify({
                    "ok": False,
                    "status": "csrf_error",
                    "message_ar": "انتهت صلاحية نموذج الحماية. حدّث الصفحة وحاول مرة أخرى.",
                }), 400
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

    @app.after_request
    def _security_headers(response):
        # Baseline hardening applied to every response. Deliberately NOT a
        # Content-Security-Policy (the panel relies on inline scripts/styles;
        # a strict CSP is a separate project) and NOT Cross-Origin-Opener-Policy
        # (it breaks the WhatsApp/Facebook OAuth popup postMessage flow).
        from flask import request as _r
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Clickjacking: allow same-origin framing (the hotspot designer previews
        # a same-origin route in an iframe) but block cross-origin embedding.
        # Store API is JSON consumed cross-origin by the router captive page —
        # framing is meaningless there, so leave those responses untouched.
        if not (_r.path or "").startswith("/api/v1/store/"):
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        # HSTS only over an already-secure connection. Browsers ignore it on
        # plain http, so this never traps a local http dev setup; https users
        # get the upgrade lock. No includeSubDomains/preload — avoids clobbering
        # an unrelated http subdomain on first rollout.
        if _r.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000")
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


def admin_login_secret_path() -> str:
    """المسار السرّي لدخول مالك المنصّة، أو '' إن لم يُضبط.

    MT37 — يُضبط بمتغيّر البيئة ``HOBERADIUS_ADMIN_LOGIN_PATH`` (لا في
    الكود ولا في git: سرٌّ يخصّ كل نشرة). حين يُضبط:
      • تُخدَم صفحة الدخول من هذا المسار،
      • ويُغلق ``/admin/radius/login`` على الجذر بـ404 — وإلّا كان
        الإخفاء مسرحيًّا: المسار الأصليّ يبقى مفتوحًا لمن يَمسح.

    ⚠ حدّ هذا الإجراء بصراحة: الرابط يُسرَّب في سجلّ المتصفّح والمفضّلة
    وسجلّات الخادم والوسيط. هو يُقلّل ضجيج الروبوتات، وليس بديلًا عن
    كبح المحاولات في ``services/login_throttle.py``.
    """
    raw = (os.environ.get("HOBERADIUS_ADMIN_LOGIN_PATH") or "").strip()
    if not raw:
        return ""
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.rstrip("/") or ""


def _register_root(app: Flask) -> None:
    # صفحة الهبوط العامّة — بوّابة أمامية قياسيّة لكلّ نسخة عميل («الواجهة
    # تكون لكل الريدياسات»). كانت الجذر يُعيد التوجيه للوحة الإدارة مباشرةً؛
    # الآن يعرض صفحة هبوط عامّة (بلا دخول، بلا بيانات) بثلاثة مداخل إلى صفحات
    # الدخول الفعليّة: الإدارة / بوّابة المشتركين / سوق البطاقات. الهويّة من
    # معالج السياق cfg (system.name / branding.*) مع احتياط رشيق إن لم تُضبط —
    # فتُصيَّر كاملةً حتى قبل ربط الترخيص. لوحة الإدارة تبقى على مسارها.
    # MT36 — الجذر صار ثلاثة وجوه بحسب السياق:
    #   • /<slug>/      → صفحة الشبكة: المداخل الثلاثة باسمها (public_landing + tenant)
    #   • / في الاستضافة → صفحة المنصّة البيعيّة (platform_landing): دخول + طلب اشتراك
    #   • / في نسخة مرخّصة أحاديّة → السلوك القديم حرفيًّا، بلا أيّ مساس
    @app.get("/")
    def _root():
        from flask import g, request as _req
        try:
            if _req.environ.get("hoberadius.tenant_slug"):
                return render_template("public_landing.html",
                                       tenant=getattr(g, "tenant", None))
            from app.radius.core.hosting_mode import open_hosting
            if open_hosting():
                # MT57 — قسم العروض يُدار من لوحة المزوّد. نُهيّئ كل عرضٍ
                # بصفوف مدده وسعر وحدته جاهزةً (القالب لا يَحسب مالًا).
                offers = []
                try:
                    from app.radius.services import pricing_offers as _po
                    for _o in _po.get_offers(visible_only=True):
                        _o = dict(_o)
                        _o["rows"] = _po.offer_rows(_o)
                        _o["unit"] = _po.unit_price(_o)
                        offers.append(_o)
                except Exception:  # noqa: BLE001 — العروض تحسين، لا تُسقط الصفحة
                    offers = []
                return render_template("platform_landing.html", offers=offers)
            return render_template("public_landing.html", tenant=None)
        except Exception:  # noqa: BLE001 — الجذر لا يقع أبدًا؛ احتياط = سلوك قديم
            return redirect(url_for("radius.dashboard"))

    # MT37 — الرابط السرّي: يَخدم صفحة الدخول نفسها من مسارٍ لا يُعلَن.
    _secret = admin_login_secret_path()
    if _secret:
        def _secret_admin_login():
            from app.radius.routes.auth import auth_login
            return auth_login()

        app.add_url_rule(_secret, "secret_admin_login", _secret_admin_login,
                         methods=["GET", "POST"])

        @app.before_request
        def _close_default_admin_login():
            """يُغلق باب الدخول المُعلَن على الجذر ما دام السرّي مضبوطًا.

            الشبكات لا تتأثّر: ``/<slug>/admin/radius/login`` يبقى مفتوحًا
            لمدرائها — بابهم مُعرَّف بشبكتهم أصلًا، والسرّية هنا لباب
            المنصّة وحده.
            """
            from flask import abort, request as _r
            if _r.environ.get("hoberadius.tenant_slug"):
                return None
            if (_r.path or "").rstrip("/") == "/admin/radius/login":
                abort(404)
            return None

    @app.before_request
    def _provider_surfaces_root_only():
        """أسطح المزوّد (``radius.provider_*``) عامّةٌ للمالك على الجذر —
        لا تخصّ جهةً بعينها. داخل سياق جهة (``/<slug>/...``) يُبادئ Werkzeug
        كلَّ ``url_for`` بالـslug تلقائيًّا (SCRIPT_NAME)، فيَصير رابط لوحة
        المزوّد ``/<slug>/admin/radius/provider`` — يلتبس على المالك ويوسم
        سطحًا عامًّا بجهةٍ لا تخصّه. نُعيد التوجيه للمسار **بلا slug** (جذر
        المضيف). آمنٌ من الحلقات: على الجذر لا slug فلا توجيه."""
        from flask import request as _r
        ep = _r.endpoint or ""
        if ep.startswith("radius.provider_") and _r.environ.get("hoberadius.tenant_slug"):
            path = _r.environ.get("PATH_INFO", "/") or "/"
            qs = _r.environ.get("QUERY_STRING", "")
            return redirect(path + (("?" + qs) if qs else ""))
        return None

    @app.post("/signup-request")
    def signup_request():
        """طلب اشتراك من صفحة المنصّة — زائر مجهول، بلا جلسة إدارة.

        لا يُنشئ شبكةً ولا حسابًا: يُسجّل الطلب ويُشعر المالك ليُراجع
        (قرار المالك 2026-07-21 — الطلب لا التسجيل الذاتيّ الفوريّ). حارس
        CSRF العام يَحمي هذا المسار كبقيّة الـPOST؛ التوكن يُولَّد عند
        تصيير الصفحة فيصل مع النموذج.
        """
        from flask import flash, request as _req

        network_name = (_req.form.get("network_name") or "").strip()
        contact_name = (_req.form.get("contact_name") or "").strip()
        phone = (_req.form.get("phone") or "").strip()
        if not (network_name and contact_name and phone):
            flash("اسم الشبكة واسم المسؤول ورقم الجوال حقولٌ مطلوبة.", "error")
            return redirect(url_for("_root") + "#signup")

        try:
            from app.radius.db.repos import signup_requests_repo
            req_id = signup_requests_repo.create(
                network_name=network_name,
                slug_wanted=(_req.form.get("slug_wanted") or "").strip(),
                contact_name=contact_name,
                phone=phone,
                email=(_req.form.get("email") or "").strip(),
                note=(_req.form.get("note") or "").strip(),
                # MT58 — سعةٌ يَذكرها الطالب (0 = لم يُحدَّد): تُقابَل بعروض
                # الأسعار وحدّ الراوترات فيُسعَّر الطلب بلا مراسلةٍ إضافيّة.
                wanted_concurrent=_req.form.get("wanted_concurrent"),
                wanted_routers=_req.form.get("wanted_routers"),
                source_ip=(_req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                           or _req.remote_addr or ""),
            )
        except Exception:  # noqa: BLE001
            app.logger.exception("signup_request: failed to store request")
            flash("تعذّر إرسال الطلب الآن. حاول مرّة أخرى أو تواصل معنا مباشرةً.", "error")
            return redirect(url_for("_root") + "#signup")

        # الإشعار = شارة العدّ المُعلَّق في لوحة الاستضافة (provider_home).
        # لم نُوصّل هذا بنظام admin_alerts عمدًا: ذاك النظام مبنيّ على مفاتيح
        # AlertSpec مُسجَّلة لكل جهة، وطلب الاشتراك حدثٌ على مستوى المنصّة
        # لا جهة له بعد — فربطه هناك يحتاج مفهومًا جديدًا لا اختصارًا.
        app.logger.info("signup_request stored: id=%s network=%r", req_id, network_name)

        flash("وصلنا طلبك. سنتواصل معك قريبًا على الرقم الذي أدخلته.", "success")
        return redirect(url_for("_root") + "#signup")


# ─── «انتهى اشتراكك» captive auto-redirect (phase 2, opt-in) ───────────────
# When an expired user (in hr-pool-expired) has their HTTP dst-nat'd to the
# panel, the request arrives with a FOREIGN Host header (the site they tried to
# open) — never the panel's own host. If enabled, we 302 such foreign-host GETs
# to the configured «انتهى اشتراكك» page, so the OS captive-portal browser pops
# the renewal page automatically.
#
# DEFAULT OFF + fail-safe: panel hosts (PUBLIC_IP/HOST + the block-page host +
# localhost) are whitelisted, and panel prefixes (/admin /api /static /portal
# /p/ …) are never touched. Any error → no redirect. So a normal panel request
# can never be hijacked.
def _install_captive_redirect(app: Flask) -> None:
    from flask import request, redirect as _redirect
    _EXCLUDED = ("/p/expired", "/p/", "/static", "/admin", "/api",
                 "/portal", "/.well-known", "/favicon")

    @app.before_request
    def _captive_redirect():
        try:
            from app.radius.core import env_settings
            if not env_settings.get_bool("HOBERADIUS_CAPTIVE_REDIRECT_ENABLED", False):
                return None
            url = str(env_settings.env("HOBERADIUS_BLOCK_PAGE_URL", "") or "").strip()
            if not url or request.method != "GET":
                return None
            path = request.path or "/"
            if path.startswith(_EXCLUDED):
                return None
            # whitelist our own hosts so panel access is never redirected
            known = {"localhost", "127.0.0.1"}
            for k in ("HOBERADIUS_PUBLIC_IP", "HOBERADIUS_PUBLIC_HOST"):
                v = str(env_settings.env(k, "") or "").strip().lower()
                if v:
                    known.add(v)
            try:
                from urllib.parse import urlparse
                bh = (urlparse(url).hostname or "").lower()
                if bh:
                    known.add(bh)        # the block-page host = usually the panel IP
            except Exception:
                pass
            host = (request.host or "").split(":", 1)[0].lower()
            if not host or host in known:
                return None              # our own host → leave it alone
            return _redirect(url, code=302)   # foreign host → captured → renew page
        except Exception:
            return None                  # never break a request
