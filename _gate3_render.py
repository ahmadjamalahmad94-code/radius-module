"""Styled render-check for the gate-3 batch (6 branches) via Flask + Playwright."""
from __future__ import annotations
import sys, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from app import create_app  # noqa

PAGES = [
    ("cards",        "/admin/radius/cards",              "cards (fix 500 carried)"),
    ("print",        "/admin/radius/print-templates",    "card designer"),
    ("sections",     "/admin/radius/sections",           "section flags (branch 5)"),
    ("envsettings",  "/admin/radius/settings/system",    "system+env settings (branch 6)"),
    ("adminbridge",  "/admin/radius/admin-bridge",       "admin bridge (branches 1+3)"),
    ("network_policy", "/admin/radius/network-policy",   "network policy (branch 2 no-mock)"),
    ("plans",        "/admin/radius/plans",              "plans list (branch 4 service-spec)"),
    ("port_services","/admin/radius/port-script-services", "port services (branch 4 spec modal)"),
]


def main() -> int:
    app = create_app()
    srv = make_server('127.0.0.1', 0, app)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    with app.test_request_context():
        from flask import session
        from flask.sessions import SecureCookieSessionInterface
        session['is_super_admin'] = True
        session['admin_id'] = 1
        session['tenant_id'] = 1
        session['admin_name'] = 'integrator'
        sci = SecureCookieSessionInterface()
        cookie = sci.get_signing_serializer(app).dumps(dict(session))

    fail = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="ar-SA")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/",
                          "httpOnly": True, "sameSite": "Lax"}])
        page = ctx.new_page()
        for slug, path, label in PAGES:
            try:
                resp = page.goto(base + path, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else 0
            except Exception as e:
                print(f"  ERR {slug}: {e}")
                status = 0
            try:
                page.screenshot(path=str(HERE / f"_gate3_{slug}.png"), full_page=False)
            except Exception:
                pass
            flag = "OK " if status == 200 else "ERR"
            if status != 200:
                fail += 1
            print(f"{flag} {slug:14s} {path:40s} status={status} ({label})")
        browser.close()
    srv.shutdown()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
