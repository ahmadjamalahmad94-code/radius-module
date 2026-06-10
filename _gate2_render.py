"""Styled render-check for the gate-2 batch via real Flask + Playwright."""
from __future__ import annotations
import sys, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from app import create_app  # noqa

PAGES = [
    ("cards",          "/admin/radius/cards",            "cards (was 500)"),
    ("print_designer", "/admin/radius/print-templates",  "card designer"),
    ("admins",         "/admin/radius/admins",           "admins list"),
    ("settings",       "/admin/radius/settings",         "settings"),
    ("tokens",         "/admin/radius/tokens",           "tokens"),
    ("wallets",        "/admin/radius/finance/wallets",  "wallets sanity"),
]


def main() -> int:
    app = create_app()
    srv = make_server('127.0.0.1', 0, app)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    # Build session cookie inline so the browser can hit admin pages.
    with app.test_request_context():
        from flask import session
        from flask.sessions import SecureCookieSessionInterface
        session['is_super_admin'] = True
        session['admin_id'] = 1
        session['tenant_id'] = 1
        session['admin_name'] = 'integrator'
        sci = SecureCookieSessionInterface()
        cookie = sci.get_signing_serializer(app).dumps(dict(session))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900},
                                  locale="ar-SA")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/",
                          "httpOnly": True, "sameSite": "Lax"}])
        page = ctx.new_page()
        for slug, path, label in PAGES:
            try:
                resp = page.goto(base + path, wait_until="domcontentloaded",
                                 timeout=15000)
                status = resp.status if resp else 0
            except Exception as e:
                print(f"  ERR {slug}: {e}")
                status = 0
            try:
                page.screenshot(path=str(HERE / f"_gate2_{slug}.png"),
                                full_page=False)
            except Exception as e:
                print(f"  shot fail {slug}: {e}")
            flag = "OK " if status == 200 else "ERR"
            print(f"{flag} {slug:18s} {path:40s} status={status} ({label})")
        browser.close()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
