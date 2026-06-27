# -*- coding: utf-8 -*-
"""لقطة صفحة «التحكم بالدخول» — جوّال + سطح مكتب (مراجعة التصميم/التباين)."""
import os, sys, tempfile, threading
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
                  HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                  HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
from app.radius.db.connection import reset_for_tests
reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    run_pending_migrations()
    from app.radius.db.repos import admins_repo, tenants_repo, access_blocks_repo
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    adm = admins_repo.create_admin(username="ac_super", password="x",
                                   full_name="m", is_super_admin=True)
    aid = adm.id
    # تعليقات وصول (suspension) + حظور أمنية (block) عَيّنة لإظهار الجداول/المؤشّرات.
    access_blocks_repo.create_block(tenant_id=1, block_type="subscriber", target="ahmad99",
                                    reason="إيقاف مؤقت للمراجعة", duration_mode="permanent",
                                    layer="suspension")
    access_blocks_repo.create_block(tenant_id=1, block_type="all_hotspot",
                                    reason="صيانة مجدولة", duration_mode="daily_window",
                                    window_start="02:00", window_end="05:00", layer="suspension")
    access_blocks_repo.create_block(tenant_id=1, block_type="ip", target="203.0.113.55",
                                    reason="محاولات دخول مشبوهة", duration_mode="permanent",
                                    source="manual", layer="block")
    access_blocks_repo.create_block(tenant_id=1, block_type="mac", target="AA:BB:CC:DD:EE:FF",
                                    reason="حظر تلقائي (fail2ban)", duration_mode="until",
                                    expires_at="2026-12-31T00:00:00Z", source="auto", layer="block")

from flask.sessions import SecureCookieSessionInterface
serializer = SecureCookieSessionInterface().get_signing_serializer(app)
cookie_val = serializer.dumps({"admin_id": aid, "admin_user": "ac_super",
                               "is_super_admin": True, "tenant_id": 1})
from werkzeug.serving import make_server
PORT = 5605
srv = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/admin/radius/access-control" % PORT
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                          is_mobile=True, has_touch=True, locale="ar")
        m.add_cookies([{"name": "session", "value": cookie_val, "domain": "127.0.0.1", "path": "/"}])
        mp = m.new_page(); mp.goto(URL, wait_until="networkidle", timeout=25000); mp.wait_for_timeout(500)
        ov = mp.evaluate("Math.max(0, (document.scrollingElement||document.documentElement).scrollWidth - (document.scrollingElement||document.documentElement).clientWidth)")
        print("mobile overflowX:", ov)
        mp.screenshot(path="C:/Projects/_review_access_control_390.png", full_page=True)
        d = b.new_context(viewport={"width": 1280, "height": 1000}, device_scale_factor=1, locale="ar")
        d.add_cookies([{"name": "session", "value": cookie_val, "domain": "127.0.0.1", "path": "/"}])
        dp = d.new_page(); dp.goto(URL, wait_until="networkidle", timeout=25000); dp.wait_for_timeout(500)
        dp.screenshot(path="C:/Projects/_review_access_control_desktop.png", full_page=True)
        b.close()
    print("done")
finally:
    srv.shutdown()
