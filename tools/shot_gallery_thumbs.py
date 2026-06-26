# -*- coding: utf-8 -*-
"""لقطة معرض التصاميم — للتحقّق من أنّ مصغّرات البطاقات حيّة (لا فارغة).

يُشغّل الخادم، يَحقن جلسة super-admin، يفتح المصمّم، يمرّ على التبويبات السبعة
ويَعدّ المصغّرات المُحمّلة في كلٍّ، ثم يُصوّر تبويب «مطعم» (تصاميم مُصوَّرة) إلى
C:/Projects/_review_gallery_thumbs.png.
"""
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
    from app.radius.db.connection import transaction
    from app.radius.db.repos import admins_repo, tenants_repo
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    adm = admins_repo.create_admin(username="gth_super", password="x",
                                   full_name="m", is_super_admin=True)
    with transaction() as c:
        c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                  "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                  ("rtr", "r1", "10.0.0.1", "s", "mikrotik", "other", "2026-01-01"))
    aid = adm.id

from flask.sessions import SecureCookieSessionInterface
serializer = SecureCookieSessionInterface().get_signing_serializer(app)
cookie_val = serializer.dumps({"admin_id": aid, "admin_user": "gth_super",
                               "is_super_admin": True, "tenant_id": 1})
from werkzeug.serving import make_server
PORT = 5601
srv = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/admin/radius/mt/1/login-designer" % PORT
OUT = os.path.join(REPO, "preview", "gallery"); os.makedirs(OUT, exist_ok=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1040, "height": 1000},
                            device_scale_factor=1, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie_val,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle", timeout=30000)
        tabs = pg.eval_on_selector_all("[data-mtld-gtab]",
                                       "els => els.map(e => e.getAttribute('data-mtld-gtab'))")
        report = {}
        for key in tabs:
            pg.click("[data-mtld-gtab='%s']" % key)
            pg.wait_for_timeout(1600)
            counts = pg.evaluate("""(k) => {
              const sec = document.querySelector(`[data-mtld-gsec="${k}"]`);
              if (!sec) return null;
              const frames = sec.querySelectorAll('.mtld-thumb-frame');
              const withSrc = sec.querySelectorAll('.mtld-thumb-frame[src]');
              const mocks = sec.querySelectorAll('.mtld-mock');
              return {iframes: frames.length, loaded: withSrc.length, customMocks: mocks.length};
            }""", key)
            report[key] = counts
        print("PER-TAB:", report)
        # صوّر تبويب «مطعم» (تصاميم مُصوَّرة واضحة).
        pg.click("[data-mtld-gtab='restaurant']")
        pg.wait_for_timeout(2500)
        sec = pg.query_selector("[data-mtld-gsec='restaurant']")
        sec.scroll_into_view_if_needed()
        pg.wait_for_timeout(500)
        sec.screenshot(path="C:/Projects/_review_gallery_thumbs.png")
        sec.screenshot(path=os.path.join(OUT, "gallery_restaurant.png"))
        b.close()
    print("done")
finally:
    srv.shutdown()
