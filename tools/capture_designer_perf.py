# -*- coding: utf-8 -*-
"""لقطة «مصمّم صفحة الدخول» بعد إصلاح الأداء — مصغّرات خفيفة بلا iframes.

يُشغّل التطبيق الحقيقي + يزرع راوترًا + جلسة سوبر، ويلتقط لقطة سطح مكتب
لصفحة المصمّم لإثبات أن المعرض يبدو جيّدًا بالمصغّرات الساكنة، ويطبع عدد
الـiframes الحيّة على الصفحة (يجب = 1).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from uuid import uuid4

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
OUT_DIR = os.path.join(REPO, "preview", "designer_perf")
PORT = 5479


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO admins(id,username,password_hash,"
                "full_name,is_super_admin,enabled,created_at) "
                "VALUES(1,'preview','x','معاينة',1,1,'2026-01-01')")
            c.execute(
                "INSERT INTO nas_devices(id,tenant_id,name,address,secret,"
                "vendor,nas_type,enabled,created_at,connection_mode) "
                "VALUES(1,1,'راوتر المعرض','203.0.113.30','s','mikrotik',"
                "'hotspot',1,?,'direct')", (now,))


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="designer_perf_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    _seed(app)
    cookie = _cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)
    os.makedirs(OUT_DIR, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 1100},
                            device_scale_factor=1, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        url = f"http://127.0.0.1:{PORT}/admin/radius/mt/1/login-designer"
        resp = pg.goto(url, wait_until="load", timeout=30000)
        pg.wait_for_timeout(1500)
        live = pg.evaluate("() => document.querySelectorAll('iframe').length")
        mocks = pg.evaluate("() => document.querySelectorAll('.mtld-mock').length")
        print(f"status={resp.status if resp else '?'} live_iframes={live} mockups={mocks}")
        # لقطة كاملة + لقطة مقصوصة على قسم المعرض
        pg.screenshot(path=os.path.join(OUT_DIR, "designer_full.png"),
                      full_page=True)
        gal = pg.query_selector(".mtld-gallery")
        if gal:
            gal.screenshot(path=os.path.join(OUT_DIR, "gallery_library.png"))
        vgrid = pg.query_selector(".mtld-vgrid")
        if vgrid:
            vgrid.screenshot(path=os.path.join(OUT_DIR, "gallery_vertical.png"))
        b.close()
    srv.shutdown()
    print(f"Saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
