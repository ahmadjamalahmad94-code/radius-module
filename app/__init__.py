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
    _install_api_cors(app)
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
                                  start_device_fingerprint_worker,
                                  start_lifecycle_worker,
                                  start_mt_reconciler,
                                  start_stale_session_reaper,
                                  start_sync_worker)
        start_sync_worker()
        start_accounting_puller()
        start_stale_session_reaper()
        start_device_fingerprint_worker()
        start_lifecycle_worker()
        start_mt_reconciler()
    except Exception:  # noqa: BLE001
        app.logger.exception("workers start failed")


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

    @app.context_processor
    def _inject():
        from flask import session as flask_session
        return {
            "csrf_token": csrf_token,
            "csrf_token_input": csrf_token_input,
            "session": flask_session,
            "admin_page_guide": lambda *a, **k: {"title": "", "steps": [], "tips": [], "links": []},
        }

    # No-op arabize filters (HobeHub يحوّلها لأسماء عربية)
    app.jinja_env.filters.setdefault("arabize", lambda s: s)
    app.jinja_env.filters.setdefault("arabize_audit", lambda s: s)

    # endpoints مستثناة من CSRF (بوّابات دخول مع credentials check)
    _CSRF_EXEMPT_PATHS = {
        "/admin/radius/login",   # login بوّابة بحد ذاتها + cookie قد لا يكون موجودًا
        "/admin/radius/logout",
        # designer-svg: same-origin live SVG preview endpoint posted to
        # on every form keystroke. Read-only render — never mutates DB.
        # Without the exemption every keystroke would 302 to login.
        "/admin/radius/print-templates/designer-svg",
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
            return ("CSRF failed — حدّث الصفحة وحاول مرة أخرى", 400)
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


# ─────────────── radius blueprint ───────────────


def _register_radius(app: Flask) -> None:
    from app.radius.routes import get_radius_blueprint
    app.register_blueprint(get_radius_blueprint())


def _register_api(app: Flask) -> None:
    from app.api import get_api_blueprint
    app.register_blueprint(get_api_blueprint())


# ─────────────── root ───────────────


def _register_root(app: Flask) -> None:
    @app.get("/")
    def _root():
        return redirect(url_for("radius.dashboard"))
