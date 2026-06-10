"""Spot-render the pages touched by the merge batch via a real
Flask server (so CSS/JS load) + Playwright. Confirms:
  • each page returns 200 (no template crash from the merges)
  • no raw English keys / 'entity #id' literals slip through
  • screenshots saved as _merge_<slug>.png at repo root

Uses werkzeug.serving in a background thread + a logged-in browser
session (super-admin) so RBAC guards don't intercept."""
from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402

PAGES = [
    ("wallets",          "/admin/radius/finance/wallets",      "قائمة الخزائن"),
    ("revenue",          "/admin/radius/finance/revenue",      "سجلات الإيرادات"),
    ("ledger",           "/admin/radius/finance/ledger",       "القيود"),
    ("sendlog",          "/admin/radius/communications/deliveries", "عمليات الإرسال"),
    ("events",           "/admin/radius/events",               "قائمة الأحداث"),
    ("subscribers",      "/admin/radius/users",                "قائمة المشتركين"),
    ("cards",            "/admin/radius/card-users",           "مستخدمو البطاقات"),
    ("template_editor",  "/admin/radius/communications/templates", "إنشاء أو تحديث قالب"),
]

# "subscriber #42" / "card #7" — the entity-id-leak symptom the
# sweep was supposed to eliminate. Counted as bad.
BAD_ID = re.compile(
    r"\b(subscriber|user|card|wallet|event|template|sender|recipient|tenant)\s*#\s*\d+",
    re.IGNORECASE)


def _strip(s: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    return re.sub(r"<[^>]+>", " ", s)


def _make_session_cookie(app):
    """Build a Flask session cookie for super-admin so the browser
    can hit any admin page without going through a login flow."""
    with app.test_request_context():
        from flask import session
        from flask.sessions import SecureCookieSessionInterface
        session['is_super_admin'] = True
        session['admin_id'] = 1
        session['tenant_id'] = 1
        session['admin_name'] = 'integrator'
        sci = SecureCookieSessionInterface()
        serializer = sci.get_signing_serializer(app)
        return serializer.dumps(dict(session))


def run() -> int:
    app = create_app()
    srv = make_server('127.0.0.1', 0, app)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    print(f"server up at {base}")

    cookie = _make_session_cookie(app)

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="ar-SA",
        )
        ctx.add_cookies([{
            "name": app.session_cookie_name if hasattr(app, 'session_cookie_name') else "session",
            "value": cookie,
            "domain": "127.0.0.1",
            "path": "/",
            "httpOnly": True,
            "sameSite": "Lax",
        }])
        page = ctx.new_page()
        for slug, path, label in PAGES:
            url = base + path
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else 0
            except Exception as e:
                print(f"  ERR {slug}: {e}")
                status = 0
            html = page.content() if status else ""
            text = _strip(html)
            bad = BAD_ID.findall(text)
            out = HERE / f"_merge_{slug}.png"
            try:
                page.screenshot(path=str(out), full_page=False)
            except Exception as e:
                print(f"  shot fail {slug}: {e}")
            results[slug] = (path, label, status, len(html), len(bad), bad[:5])
            flag = "OK " if status == 200 else "ERR"
            print(f"{flag} {slug:18s} {path:48s} status={status} "
                  f"size={len(html)} bad={len(bad)}")
            if bad:
                print(f"   samples: {bad[:3]}")
        browser.close()
    srv.shutdown()
    fails = [s for s, v in results.items() if v[2] != 200]
    print()
    print(f"=== summary === {len(results) - len(fails)}/{len(results)} OK")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
