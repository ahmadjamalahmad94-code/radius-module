"""Shared helpers for the fix-wave-3 web-UI tests (stream «webui»).

* ``make_app()`` — one Flask app on a private temp DB (workers off, no seed).
* ``seed_world()`` — owner + plan + subscriber + NAS + card batch (+ cards).
* ``login_session(client, admin_id)`` — the exact session keys a real web
  login writes (CSRF token «tok», sent back by the test client header).
* ``role_admin(perms)`` — a manager whose role holds exactly ``perms``.
* ``BrowserProxy`` — serves the Flask test client to a real Chromium page via
  Playwright request interception, so page JavaScript runs against the real
  app (no server process, no network).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

PW = "Pass-12345"
BASE = "http://hr.test"


def make_app():
    tmp = tempfile.mkdtemp(prefix="hr_webui_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
    os.environ.pop("HOBERADIUS_API_TOKENS", None)
    os.environ["HOBERADIUS_BOOTSTRAP_ADMIN_USER"] = "owner_root"
    os.environ["HOBERADIUS_BOOTSTRAP_ADMIN_PASS"] = PW
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True

    # Tests keep ONE app context open around many requests; Flask then reuses
    # it for every request, so flask.g (the per-request caches: grants, perms,
    # can_submit…) would leak from one manager's request into the next. Real
    # requests each get a fresh context — mimic that.
    def _fresh_g():
        from flask import g
        for k in list(vars(g)):
            try:
                delattr(g, k)
            except Exception:  # noqa: BLE001
                pass
    a.before_request_funcs.setdefault(None, []).insert(0, _fresh_g)
    with a.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        if admins_repo.primary_admin_id() is None:
            admins_repo.create_admin(username="owner_root", password=PW,
                                     full_name="المالك", is_super_admin=True)
    return a


def db():
    from app.radius.db.connection import db as _db
    return _db()


def owner_id() -> int:
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def role_admin(perms, *, name=None, full_name="مدير اختبار"):
    from app.radius.db.repos import admins_repo
    r = admins_repo.create_role(name="r_" + uuid4().hex[:8], display_name="R",
                                permissions=tuple(perms))
    return admins_repo.create_admin(username=name or ("m_" + uuid4().hex[:8]),
                                    password=PW, full_name=full_name,
                                    is_super_admin=False, role_id=r.id)


def login_session(client, admin_id):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
    with client.session_transaction() as s:
        s["admin_id"] = a.id
        s["admin_user"] = a.username
        s["admin_name"] = a.username
        s["is_super_admin"] = bool(_resolve_is_super(a))
        s["tenant_id"] = 1
        s["admin_sv"] = admins_repo.session_epoch(a.id) or 0
        s["admin_av"] = admins_repo.authz_epoch(a.id)
        s["permissions"] = list(admins_repo.admin_permissions(a))
        s["_csrf_token"] = "tok"
    client.environ_base["HTTP_X_CSRFTOKEN"] = "tok"


def plan(price=30.0, days=30, name=None) -> int:
    now = datetime.utcnow().isoformat()
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, quota_total_mb, enabled, created_at, updated_at) "
        "VALUES(1,?,?,?,?,?,0,1,?,?)",
        (name or ("p_" + uuid4().hex[:6]), days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


def subscriber(plan_id=None, *, username=None, manager_id=None, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or ("u_" + uuid4().hex[:8])
    fields = dict(id=None, tenant_id=1, username=username, password="secret1",
                  plan_id=plan_id, full_name="مشترك تجربة", mobile="0599000000",
                  status="enabled", expire_at=datetime.utcnow() + timedelta(days=10),
                  manager_id=manager_id)
    fields.update(kw)
    subscribers_repo.upsert_subscriber(Subscriber(**fields))
    return username


def card_batch(plan_id, count=12, *, package_name="باقة ساعة"):
    """A real generated card batch (service path) → (batch_id, [card ids])."""
    from app.radius.services.cards import get_cards_service
    svc = get_cards_service()
    batch, cards = svc.generate_batch(actor="owner_root", plan_id=plan_id,
                                      count=count, package_name=package_name)
    return int(batch.id), [int(getattr(c, "id", 0) or c["id"]) for c in cards]


class BrowserProxy:
    """Route every request of a Playwright page to the Flask test client.

    Cookies are kept by the browser: the proxy forwards the Cookie header and
    returns Set-Cookie, so the session behaves exactly like a real browser's.
    """

    def __init__(self, app, page):
        self.app = app
        self.client = app.test_client(use_cookies=False)
        self.page = page
        page.route(BASE + "/**", self._handle)
        # static CDN assets (fonts / font-awesome) are irrelevant — block fast
        page.route(lambda url: not url.startswith(BASE), lambda r: r.abort())

    def _handle(self, route):
        req = route.request
        parts = urlsplit(req.url)
        path = parts.path + (("?" + parts.query) if parts.query else "")
        headers = {k: v for k, v in req.headers.items()
                   if k.lower() not in ("host", "content-length")}
        if not any(k.lower() == "cookie" for k in headers):
            # WebKit (iPhone emulation) stores the session cookie but never
            # exposes a Cookie header on intercepted requests, so the login
            # never reached Flask and every page bounced to /login. Rebuild
            # it from the context's jar (Chromium already sends it).
            try:
                jar = self.page.context.cookies(req.url)
            except Exception:  # noqa: BLE001 — context closing; nothing to send
                jar = []
            if jar:
                headers["Cookie"] = "; ".join(
                    f"{c['name']}={c['value']}" for c in jar)
        body = req.post_data_buffer
        resp = self.client.open(path, method=req.method, headers=headers,
                                data=body, base_url=BASE)
        out_headers = {}
        for k, v in resp.headers.items():
            if k.lower() == "set-cookie":
                out_headers.setdefault("set-cookie", [])
                out_headers["set-cookie"].append(v)
            elif k.lower() not in ("content-length", "content-encoding"):
                out_headers[k] = v
        if "set-cookie" in out_headers:
            out_headers["set-cookie"] = "\n".join(out_headers["set-cookie"])
        status, body = resp.status_code, resp.get_data()
        if status in (301, 302, 303, 307, 308) and req.resource_type == "document":
            # Chromium does not re-enter the route for a fulfilled redirect's
            # target — replay it as a history-replacing navigation instead.
            import json as _json
            loc = resp.headers.get("Location", "/")
            out_headers = {k: v for k, v in out_headers.items()
                           if k.lower() not in ("location", "content-type")}
            out_headers["content-type"] = "text/html; charset=utf-8"
            body = ("<!doctype html><script>location.replace(%s)</script>"
                    % _json.dumps(loc)).encode("utf-8")
            status = 200
        route.fulfill(status=status, headers=out_headers, body=body)

    def login(self, username, password=PW):
        """Real web login through the browser (form POST, cookie set)."""
        self.page.goto(BASE + "/admin/radius/login")
        self.page.fill("input[name=username]", username)
        self.page.fill("input[name=password]", password)
        self.page.click("button[type=submit]")
        self.page.wait_for_url(lambda u: "/login" not in u)
        self.page.wait_for_load_state("domcontentloaded")
