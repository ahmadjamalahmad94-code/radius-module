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
    # «٣٥٫٥» من لوحة عربيّة ⇒ «35.5» في نماذج الويب قبل أيّ مسار (R12 N1).
    from .radius.core.form_numbers import install_form_number_normalizer
    install_form_number_normalizer(app)
    _install_api_cors(app)
    _install_store_cors(app)
    _install_store_key_guard(app)
    # Several processes boot at once (panel workers + the worker process):
    # migrations and seeding take turns (file lock next to the DB).
    from app.radius.db.connection import boot_lock
    with boot_lock():
        _init_db(app)
    _install_tenant(app)
    _install_npc_live_adapters(app)
    _register_radius(app)
    _register_api(app)
    _register_root(app)
    _install_captive_redirect(app)
    _install_cli(app)
    _install_mt_health_context(app)
    with boot_lock():
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
                "Authorization, Content-Type, X-Request-Id, Idempotency-Key"
            )
            resp.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, PATCH, DELETE, OPTIONS"
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


def _tr069_enabled_flag() -> bool:
    """علم الوحدة التجريبيّة لإدارة الراوترات (TR-069) — للقوالب. آمن دائمًا."""
    try:
        from app.radius.services.tr069 import config as _tr069_cfg
        return _tr069_cfg.tr069_enabled()
    except Exception:  # noqa: BLE001
        return False


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
                                  start_notification_sounds_worker,
                                  start_self_update_worker,
                                  start_speed_split_worker,
                                  start_session_timer_guard,
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
    _safe_start("session_timer_guard", start_session_timer_guard)
    _safe_start("device_fingerprint", start_device_fingerprint_worker)
    _safe_start("lifecycle", start_lifecycle_worker)
    _safe_start("admin_bridge_sync", start_admin_bridge_sync_worker)
    _safe_start("mt_reconciler", start_mt_reconciler)
    # Periodic "is a newer version available?" check (opt-in self-update).
    # Degrades silently when the license bridge is off/unreachable.
    _safe_start("self_update_check", start_self_update_worker, app)
    # MT92 — سحب أصوات الإشعارات من لوحة التراخيص (ساعة). يَصمت
    # بهدوء حين يكون الجسر مُطفأً أو غير قابلٍ للوصول.
    _safe_start("notification_sounds", start_notification_sounds_worker, app)
    _safe_start("backup_scheduler", start_backup_scheduler_worker)
    _safe_start("log_retention", start_log_retention_worker)
    # WAL hygiene (stress L01): TRUNCATE checkpoint once the WAL passes 64 MB.
    try:
        from app.workers.wal_maintenance_worker import start_wal_maintenance_worker
        _safe_start("wal_maintenance", start_wal_maintenance_worker)
    except Exception:  # noqa: BLE001
        app.logger.exception("wal_maintenance worker import failed")
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
    # إدارة الراوترات عن بُعد (TR-069) — يدفع الأوامر ويُزامن حالة الأجهزة مع
    # GenieACS. معطّل داخليًّا ما لم يُفعَّل HOBERADIUS_TR069_ENABLED (وحدة تجريبيّة).
    try:
        from app.workers.tr069_action_worker import start_tr069_action_worker
        _safe_start("tr069_action", start_tr069_action_worker)
    except Exception:  # noqa: BLE001
        app.logger.exception("tr069 worker import failed")
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

    def _panel_clock() -> dict | None:
        """ساعةُ اللوحة للشريط العلويّ: {hhmm, tz, epoch, offset_min}.

        🔑 نُرسل **لحظة الخادم** (‏epoch UTC) و**إزاحة منطقة اللوحة بالدقائق**،
           فيتقدّم العدّاد في المتصفّح بلا نداءِ شبكةٍ كلّ ثانية، ويبقى المعروضُ
           توقيتَ النظام لا توقيت جهاز المستخدم (قد يكون في منطقةٍ أخرى).

        🔴 الإزاحة تُحسب لحظتَها لا تُثبَّت: مع منطقةٍ IANA تتغيّر بالتوقيت
           الصيفيّ، وتثبيتُها يُخطئ ساعةً كاملةً نصفَ العام.
        """
        try:
            from datetime import timezone as _tz

            from .radius.core.system_config import local_now

            now = local_now()
            off = now.utcoffset()
            return {
                "hhmm": now.strftime("%H:%M:%S"),
                "tz": str(now.tzinfo),
                "epoch": int(now.astimezone(_tz.utc).timestamp()),
                "offset_min": int(off.total_seconds() // 60) if off else 0,
            }
        except Exception:  # noqa: BLE001 — الساعة زينة، لا تكسر صفحةً أبدًا
            return None

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
            from app.radius.services.notifications import can_see_router_alerts
            tid = int(getattr(_g, "tenant_id", DEFAULT_TENANT_ID))
            # fix3 (F01 F13): router/system alerts only for nas.view holders.
            rows = alerts_repo.list_open(tid, limit=50) if can_see_router_alerts() else []
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
            # fix3 (F01 F9 / F08 M2): only what THIS admin may see, his reads.
            viewer = _notif.current_viewer(tid)
            return {
                "count": _notif.unread_count(tid, viewer=viewer),
                "items": _notif.recent_for_bell(tid, limit=max(1, int(limit)),
                                                viewer=viewer),
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

    def asset_v(filename: str) -> str:
        """بصمةٌ قصيرةٌ لملفٍّ ثابت — تُلحَق بالرابط ككاسرِ ذاكرة.

        نجينكس يخدم /static بـ`max-age=3600`، فإصلاحُ جافاسكربت لا
        يصل جوّالَ المشغّل قبل ساعةٍ كاملة — وهو يرى العطبَ ويظنّ
        أنّ الإصلاحَ لم يُنشَر. البصمةُ من حجم الملفّ وزمن تعديله:
        تتغيّر مع كلّ تعديلٍ فيصل فورًا، وتثبت بين التعديلات فتبقى
        فائدةُ التخزين المؤقّت.

        محصَّنة: أيُّ خطأ يردّ نصًّا فارغًا — رابطٌ بلا بصمةٍ أهونُ
        من صفحةٍ لا تُرسَم.
        """
        try:
            import os
            p = os.path.join(app.static_folder, *filename.split("/"))
            st = os.stat(p)
            return format(int(st.st_mtime) ^ int(st.st_size), "x")
        except Exception:  # noqa: BLE001
            return ""

    @app.context_processor
    def _inject():
        from flask import session as flask_session
        ctx = {
            "csrf_token": csrf_token,
            "asset_v": asset_v,
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
            # علم الوحدة التجريبيّة «إدارة الراوترات» (TR-069): يُظهر/يُخفي قسم
            # «المعمل» في الشريط. كسول وآمن — لا يكسر أي صفحة.
            "tr069_enabled": _tr069_enabled_flag,
            # تنبيهات شريط الأعلى (الجرس): دالة كسولة تُستدعى من الـ layout فقط،
            # تُرجع أحدث التنبيهات المفتوحة للمستأجر الحالي — كل عنصر يحمل id
            # للانتقال لصفحة تفاصيله. لا تكسر الصفحة أبدًا عند أي خطأ.
            "topbar_alerts": _topbar_alerts,
            # ساعة الشريط العلويّ: لحظةُ الخادم + إزاحةُ منطقة اللوحة، فتعرض
            # الواجهة **الساعة التي يحسب بها النظام** لا ساعة متصفّح المستخدم.
            # كسولة وآمنة — أيّ خطأ يُخفي الساعة ولا يكسر صفحة.
            "panel_clock": _panel_clock,
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
                if _mg.endpoint_effectively_hidden(
                        aid, endpoint, tenant_id=tid, perms=perms):
                    return True
                # D03: مدخل نموذج (إضافة/تعديل) يرفضه الحارس عند فتحه — قسمٌ
                # مقفول، أو فعل «إنشاء» غير ممنوح — يُخفى في كل مكان (السايدبار،
                # أزرار اللوحة، أزرار القائمة): نفس قرار الحارس على GET، فلا زرّ
                # يفتح نموذجًا يُرفَض حفظه.
                name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
                akey = _mg.endpoint_action(name)
                if _mg.is_form_endpoint(name) or (
                        akey and _mg.ACTION_REGISTRY.get(akey, {}).get("gate_get")):
                    from app.radius.routes.blueprint import rbac_denial_status
                    return rbac_denial_status(
                        name, "GET", is_super=False, perms=perms, admin_id=aid,
                        tenant_id=tid, record_activity=False) is not None
                return False
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

        def _sub_actions() -> dict:
            """D17: أعلام أفعال المشترك للمدير الحاليّ — نفس قرار actions-context
            في التطبيق (services/subscriber_action_flags)."""
            try:
                from app.radius.services.subscriber_action_flags import session_action_flags
                return session_action_flags()
            except Exception:  # noqa: BLE001 — fail-open (عرض)
                from collections import defaultdict
                return defaultdict(lambda: True)

        def _manager_locked_fields(entity: str) -> list:
            """D20: أسماء حقول النموذج المقفولة على المدير الحاليّ (للقراءة فقط)."""
            if _is_super():
                return []
            try:
                from app.radius.services import manager_grants as _mg
                return _mg.locked_attr_names(_sess.get("admin_id"), entity,
                                             tenant_id=int(_sess.get("tenant_id") or 1))
            except Exception:  # noqa: BLE001
                return []

        def _can_submit(endpoint: str, method: str = "POST") -> bool:
            """F01-F2: الزرّ/الرابط/النموذج يُعرَض فقط إن كان طلبه (endpoint +
            method) سيَقبله الخادم لهذا المدير — **نفس قرار** حارس اللوحة
            (routes/blueprint.rbac_denial_status: أعلام القسم، الأقسام الدقيقة،
            بوّابة الفعل، bulk.ops، مفتاح الكتابة/المالك، مفتاح العرض). لا يفتح
            نموذجٌ لا يُحفَظ، ولا زرّ يقود إلى 403. السوبر دائمًا نعم؛ فحصٌ بلا
            تسجيل حركة (record_activity=False)؛ مُخزَّن لكل طلب. fail-open عند
            خطأ داخليّ (الخادم يبقى الحَكَم)."""
            if _is_super():
                return True
            # جلسةٌ بلا مفتاح «permissions» ليست جلسة دخول حقيقيّة (كلّ دخول يكتبه
            # ولو فارغًا) — لا قرار RBAC ممكن: fail-open كبقيّة طبقة العرض.
            try:
                if "permissions" not in _sess:
                    return True
            except Exception:  # noqa: BLE001
                return True
            name = (endpoint or "").split(".", 1)[1] if (endpoint or "").startswith("radius.") else (endpoint or "")
            meth = (method or "POST").upper()
            try:
                from flask import g as _g
                cache = getattr(_g, "_can_submit_cache", None)
                if cache is None:
                    cache = {}
                    _g._can_submit_cache = cache
            except Exception:  # noqa: BLE001
                cache = {}
            key = (name, meth)
            if key in cache:
                return cache[key]
            # نموذجٌ يرسل POST إلى عنوانٍ اسمه لصفحة GET (مثل /users → users_list)
            # يصل فعليًّا إلى endpoint آخر على نفس المسار (users_create) — نحكم عليه.
            try:
                _rules = app.url_map._rules_by_endpoint.get("radius." + name) or []
                if _rules and not any(meth in (r.methods or ()) for r in _rules):
                    for _r in app.url_map.iter_rules():
                        if _r.rule == _rules[0].rule and meth in (_r.methods or ()) \
                                and _r.endpoint.startswith("radius."):
                            name = _r.endpoint.split(".", 1)[1]
                            break
            except Exception:  # noqa: BLE001
                pass
            try:
                from flask import g as _g
                from app.radius.routes.blueprint import rbac_denial_status
                _saved = getattr(_g, "_rbac_denial", None)
                try:
                    res = rbac_denial_status(
                        name, meth, is_super=False,
                        perms=_sess.get("permissions") or [],
                        admin_id=_sess.get("admin_id"),
                        tenant_id=int(_sess.get("tenant_id") or 1),
                        record_activity=False) is None
                finally:
                    # لا نطمس سبب رفضٍ حقيقيّ تعرضه صفحة 403 نفسها.
                    _g._rbac_denial = _saved
            except Exception:  # noqa: BLE001 — fail-open (العرض فقط)
                res = True
            # رابط «جديد/تعديل» يفتح نموذجًا: يُعرَض فقط إن كان حفظه مقبولًا أيضًا
            # (bw_new يفتح بـplans.create وحفظه bw_create يطلب plans.edit).
            if res and meth == "GET":
                pair = (name[:-4] + "_create" if name.endswith("_new")
                        else name[:-5] + "_update" if name.endswith("_edit") else "")
                if pair and ("radius." + pair) in app.view_functions:
                    cache[key] = res     # guard against a self-cycle
                    res = _can_submit(pair, "POST")
            cache[key] = res
            return res

        def _can_any(*endpoints: str) -> bool:
            return any(_can_submit(e) for e in endpoints)

        def _can_open_mt(endpoint: str) -> bool:
            """صفحات طبقة mikrotik.* القديمة (مزخرف mt_permissions.requires_perm):
            حارس اللوحة + نفس فحص المزخرف. تُستعمل حيث يَظهر رابطها لكلّ مدير
            (جرس تنبيهات الراوترات في الشريط العلويّ — F01-F13)."""
            if not _can_submit(endpoint, "GET"):
                return False
            if _is_super():
                return True
            try:
                name = endpoint if endpoint.startswith("radius.") else "radius." + endpoint
                _need = getattr(app.view_functions.get(name), "_hr_required_perms", None)
                if _need:
                    from app.radius.services.mt_permissions import require_perms
                    return bool(require_perms(*_need)[0])
            except Exception:  # noqa: BLE001 — fail-open
                pass
            return True

        def _can_open(url, method: str = "GET") -> bool:
            """رابطٌ عنوانه بيانات (تنبيه/إشعار/بطاقة لوحة): يُحلّ العنوان إلى
            endpoint ثم نفس قرار الحارس. عنوان خارجيّ/غير معروف ⇒ نعم."""
            if _is_super() or not url:
                return True
            try:
                from urllib.parse import urlsplit
                parts = urlsplit(str(url))
                if parts.netloc or not parts.path.startswith("/admin/radius/"):
                    return True
                ep, _args = app.url_map.bind("localhost").match(parts.path, method=method)
            except Exception:  # noqa: BLE001
                return True
            return _can_submit(ep, method)

        def _can_post_here() -> bool:
            """نموذجٌ يُرسَل إلى عنوان الصفحة نفسها (بلا action): قرار الحارس على
            الـendpoint الذي يستقبل POST لهذا العنوان."""
            if _is_super():
                return True
            try:
                from flask import request as _rq
                adapter = app.url_map.bind_to_environ(_rq.environ)
                ep, _args = adapter.match(method="POST")
            except Exception:  # noqa: BLE001 — لا مسار POST هنا: لا شيء نحجبه
                return True
            return _can_submit(ep, "POST")

        return {
            "can_submit": _can_submit,
            "can_submit_any": _can_any,
            "can_post_here": _can_post_here,
            "can_open": _can_open,
            "can_open_mt": _can_open_mt,
            "manager_locked_fields": _manager_locked_fields,
            "subscriber_actions": _sub_actions,
            "manager_nav_hidden": _manager_nav_hidden,
            "manager_section_locked": _manager_section_locked,
            "manager_can_write": _manager_can_write,
            "manager_field_locked": _manager_field_locked,
            "manager_action_allowed": _manager_action_allowed,
            "manager_can_see": _manager_can_see,
        }

    # F01-F2: the gating helpers are ALSO Jinja globals, so macros imported
    # without context (_partials/hub.html btn/action_card…) can gate their own
    # href. They only read the request-bound session/g proxies at call time.
    _gating = _inject_manager_grants()
    for _gk in ("can_submit", "can_submit_any", "can_post_here", "can_open"):
        app.jinja_env.globals.setdefault(_gk, _gating[_gk])

    import re as _re_gate
    _GATE_A = _re_gate.compile(r'<a\b([^>]*?)\bhref="([^"]*)"([^>]*)>(.*?)</a>', _re_gate.S | _re_gate.I)
    _GATE_FORM = _re_gate.compile(r'<form\b([^>]*?)\baction="([^"]*)"([^>]*)>(.*?)</form>',
                                  _re_gate.S | _re_gate.I)

    def _gate_html(html):
        """F01-F2: HTML built as a string (hero actions_html, modal footers):
        a link/form whose target the guard would refuse is dropped — the same
        decision as can_open(). Owner/super: unchanged."""
        from markupsafe import Markup
        s = str(html or "")
        if not s or ("href=" not in s and "action=" not in s):
            return html
        can_open = _gating["can_open"]

        def _form(m):
            meth = "POST" if _re_gate.search(r'method\s*=\s*"post"', m.group(1) + m.group(3), _re_gate.I) else "GET"
            return m.group(0) if can_open(m.group(2), meth) else ""

        def _a(m):
            return m.group(0) if can_open(m.group(2)) else ""
        s = _GATE_FORM.sub(_form, s)
        s = _GATE_A.sub(_a, s)
        return Markup(s)

    app.jinja_env.filters.setdefault("gate_html", _gate_html)

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
                            "tz_offset": 3.0, "tz_name": _DEFAULTS.get("billing.timezone", "Asia/Gaza"),
                            "system_name": "HobeRadius", "country": "",
                            "logo_url": "", "primary_color": "#2BAACC"}}

    from app.radius.core.system_config import (
        format_duration_days as _dur_days,
        format_money as _fmt_money,
        format_money_multi as _fmt_money_multi,
        to_local as _to_local,
        to_local_date as _to_local_date,
    )
    app.jinja_env.filters["money"] = _fmt_money
    # مبلغ لكل عملة («5,683.89 ₪ · 426.31 USD») لإجماليّات by_currency.
    app.jinja_env.filters["money_multi"] = _fmt_money_multi
    app.jinja_env.filters["dt_local"] = _to_local
    app.jinja_env.filters["date_local"] = _to_local_date
    # f06-L3: مدّة بكلماتٍ عربيّة («1 ساعة و5 دقائق») بدل «1h 5m» في الويب.
    # Imported defensively (see fmt_base_time_ar below): a formatter import
    # failure must never brick create_app().
    try:
        from app.radius.core.duration_fmt import fmt_compact_ar as _dur_ar
    except Exception:  # noqa: BLE001
        app.logger.exception("fmt_compact_ar import failed; using degraded fallback")

        def _dur_ar(seconds):
            s = max(0, int(seconds or 0))
            h, m = s // 3600, (s % 3600) // 60
            return f"{h} ساعة و{m} دقيقة" if h else f"{m} دقيقة"
    app.jinja_env.filters["dur_ar"] = _dur_ar
    # Reports / profile / portal: the same words, but a sub-minute session keeps
    # its seconds («45 ثانية») — fix3 integration.
    try:
        from app.radius.core.duration_fmt import fmt_duration_ar as _dur_ar_s
    except Exception:  # noqa: BLE001
        _dur_ar_s = _dur_ar
    app.jinja_env.filters["dur_ar_s"] = _dur_ar_s

    def _epoch(value) -> int:
        """UTC epoch seconds of a stored (naive UTC) datetime — 0 when absent or
        unrepresentable. ``dt.timestamp()`` raised OSError for a year-0001
        expiry and took the whole subscribers list down (re-test R01 N4)."""
        if not value or not hasattr(value, "timetuple"):
            return 0
        try:
            import calendar
            tt = value.utctimetuple() if getattr(value, "tzinfo", None) else value.timetuple()
            return int(calendar.timegm(tt))
        except Exception:  # noqa: BLE001
            return 0
    app.jinja_env.filters["epoch"] = _epoch
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

    # F08-L: جمعٌ عربيّ صحيح للأعداد («3 بطاقات»، «11 إعدادًا») — مصدر واحد.
    from .radius.core.ar_text import ar_count as _ar_count
    app.jinja_env.filters.setdefault("ar_count", _ar_count)
    app.jinja_env.globals.setdefault("ar_count", _ar_count)
    # F08-L: فاعل خام («api-token:72»، «unknown») → اسم عرض مقروء.
    from .radius.services.actor_names import (
        actor_display as _actor_display, humanize_actor_refs as _actor_refs)
    app.jinja_env.filters.setdefault("actor_name", _actor_display)
    app.jinja_env.filters.setdefault("actor_refs", _actor_refs)
    # حدود المالك المعروضة في الواجهة — مصدرها الخادم نفسه، لا أرقام مكرّرة في
    # القوالب. كائنٌ واحد (دمج fix3-webui + fix3-moneyquota):
    #   • ``hr_limits.extend_max_days`` / ``.extend_too_long`` — «الحدود» الحيّة
    #     (core.limits، تُقرأ كلّ طلب)؛ ``.card_username_len_max`` /
    #     ``.card_password_len_max`` — ثوابت مولّد البطاقات؛
    #   • ``hr_limits()`` — لقطة ``limits.snapshot()`` كاملة (max_extend_minutes…).
    #   • أيّ اسمٍ آخر في اللقطة متاحٌ كخاصّيّة (``hr_limits.max_loan_amount``).
    from .radius.services.cards import (
        PASSWORD_LENGTH_MAX as _pw_max, USERNAME_LENGTH_MAX as _un_max)

    class _HrLimits:
        _static = {"card_username_len_max": _un_max, "card_password_len_max": _pw_max}

        def __call__(self, tenant_id=None):
            from .radius.core import limits as _lim
            return _lim.snapshot(tenant_id)

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            if name in self._static:
                return self._static[name]
            from .radius.core import limits as _lim
            if name == "extend_max_days":
                return _lim.max_extend_days()
            if name == "extend_too_long":
                return _lim.extend_too_long_msg()
            snap = _lim.snapshot()
            if name in snap:
                return snap[name]
            raise AttributeError(name)

        def __getitem__(self, name):
            try:
                return getattr(self, name)
            except AttributeError:
                raise KeyError(name) from None

    app.jinja_env.globals["hr_limits"] = _HrLimits()

    # رقم الراوتر المعروض «#N» = ترتيبه بين راوترات المستأجر الحيّة، لا
    # المعرّف الداخليّ (AUTOINCREMENT لا يُعاد — تجارب محذوفة كانت تجعل
    # الراوتر الوحيد يظهر «#39»). المعرّف الداخليّ يبقى في الروابط (مفتاح
    # تقنيّ للحسابات rtr-<id> والملفّات) — العرض فقط ترتيبيّ.
    from .radius.db.repos.nas_repo import display_ordinal as _router_no
    app.jinja_env.globals.setdefault("router_no", _router_no)

    # «الحدود» — ``hr_limits`` (أعلاه) يعرض سقوف core.limits الحيّة للقوالب.

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
            return _csrf_failure_response()
        return None

    def _csrf_failure_response():
        """F08-L: فشل رمز الحماية — JSON عربيّ لطلبات AJAX (fetch/XHR بأيّ
        جسم)، وصفحة عربيّة منسّقة للتصفّح تحفظ ما كُتب (كانت نصًّا خامًا)."""
        from flask import request, jsonify as _jsonify, render_template as _rt
        msg = "انتهت صلاحية نموذج الحماية. حدّث الصفحة وحاول مرة أخرى."
        accept = request.headers.get("Accept") or ""
        wants_json = (
            request.is_json
            or request.headers.get("X-CSRFToken") is not None
            or (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"
            or ("application/json" in accept and "text/html" not in accept)
        )
        if wants_json:
            return _jsonify({
                "ok": False,
                "status": "csrf_error",
                "error": msg,
                "message": msg,
                "message_ar": msg,
            }), 400
        try:
            fields = {
                k: request.form.getlist(k) for k in request.form.keys()
                if k != "_csrf_token" and "password" not in k.lower()
                and "secret" not in k.lower()}
        except Exception:  # noqa: BLE001
            fields = {}
        back = request.referrer or ""
        try:
            from urllib.parse import urlparse
            _u = urlparse(back)
            # رجوعٌ داخل الموقع فقط (لا رابط خارجيّ من ترويسة Referer).
            back = (_u.path + ("?" + _u.query if _u.query else "")) if (
                not _u.netloc or _u.netloc == request.host) else ""
        except Exception:  # noqa: BLE001
            back = ""
        try:
            return _rt("radius/csrf_error.html", fields=fields,
                       back_url=back), 400
        except Exception:  # noqa: BLE001 — never 500 over a CSRF refusal
            return ('<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">'
                    "<h1>انتهت صلاحية الصفحة</h1><p>" + msg + "</p></html>",
                    400, {"Content-Type": "text/html; charset=utf-8"})

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
    def _relative_self_redirects(response):
        # Re-test R13 L5: behind nginx on :8443 the proxied Host header has no
        # port, so Werkzeug's absolute redirects (the strict-slash
        # /admin/radius → /admin/radius/ and every url_for-built Location)
        # dropped «:8443». A redirect to the SAME host and scheme is sent as a
        # relative Location; the browser resolves it against the URL it really
        # used, port included. Other hosts / schemes / explicit ports: as-is.
        loc = response.headers.get("Location")
        if not loc or not (300 <= response.status_code < 400):
            return response
        from urllib.parse import urlsplit
        from flask import request as _r
        try:
            parts = urlsplit(loc)
            if (parts.scheme and parts.netloc
                    and parts.scheme == _r.scheme
                    and (parts.hostname or "").lower()
                    == (_r.host or "").rsplit(":", 1)[0].strip("[]").lower()
                    and (parts.port is None
                         or f"{parts.hostname}:{parts.port}".lower() == (_r.host or "").lower())):
                rel = parts.path or "/"
                if parts.query:
                    rel += "?" + parts.query
                if parts.fragment:
                    rel += "#" + parts.fragment
                response.headers["Location"] = rel
        except ValueError:
            pass
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
    # Background thread → only in the process that runs the workers (not in
    # the panel/auth gunicorns started with HOBERADIUS_NO_WORKER=1).
    if (_net_ops_on and not _os.environ.get("PYTEST_CURRENT_TEST")
            and not _os.environ.get("HOBERADIUS_NO_WORKER")):
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
    # JSON 500/503 envelope under /api/ + no Infinity/NaN in JSON + release a
    # leaked SQLite transaction at request end (app/api/errors.py).
    from app.api.errors import install_api_error_handlers
    install_api_error_handlers(app)


# ─────────────── root ───────────────


def _register_root(app: Flask) -> None:
    # صفحة الهبوط العامّة — بوّابة أمامية قياسيّة لكلّ نسخة عميل («الواجهة
    # تكون لكل الريدياسات»). كانت الجذر يُعيد التوجيه للوحة الإدارة مباشرةً؛
    # الآن يعرض صفحة هبوط عامّة (بلا دخول، بلا بيانات) بثلاثة مداخل إلى صفحات
    # الدخول الفعليّة: الإدارة / بوّابة المشتركين / سوق البطاقات. الهويّة من
    # معالج السياق cfg (system.name / branding.*) مع احتياط رشيق إن لم تُضبط —
    # فتُصيَّر كاملةً حتى قبل ربط الترخيص. لوحة الإدارة تبقى على مسارها.
    @app.get("/")
    def _root():
        try:
            return render_template("public_landing.html")
        except Exception:  # noqa: BLE001 — الجذر لا يقع أبدًا؛ احتياط = سلوك قديم
            return redirect(url_for("radius.dashboard"))


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
