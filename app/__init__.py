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
    _init_db(app)
    _install_tenant(app)
    _register_radius(app)
    _register_api(app)
    _register_root(app)
    _seed_demo(app)
    _start_workers(app)
    return app


def _start_workers(app: Flask) -> None:
    """يُشغّل خيوط الخلفية لمرّة واحدة: webhook + sync + accounting puller."""
    if os.environ.get("HOBERADIUS_NO_WORKER"):
        return
    try:
        from app.webhooks.queue_worker import start_worker_once
        start_worker_once()
    except Exception:  # noqa: BLE001
        app.logger.exception("webhook worker start failed")
    try:
        from app.workers import start_sync_worker, start_accounting_puller
        start_sync_worker()
        start_accounting_puller()
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


def _seed_demo(app: Flask) -> None:
    if os.environ.get("HOBERADIUS_NO_SEED"):
        return
    try:
        from app.radius.seed import seed_demo_data
        seed_demo_data()
    except Exception:  # noqa: BLE001
        app.logger.exception("seed failed (non-fatal)")


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
