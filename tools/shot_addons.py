# -*- coding: utf-8 -*-
"""لقطة قسم «③ الإضافات» في مصمّم الدخول — للتحقّق من الأكورديون المطويّ.

يُشغّل الخادم محلّيًّا، يَحقن كوكي جلسة موقَّعة (super-admin)، يفتح الصفحة،
يُصوّر قسم الإضافات في الحالة الافتراضيّة (مطويّ/مضغوط)، ثم يَفتح أوّل إضافة
ويُصوّر مجدّدًا لإثبات الطيّ/الفتح. يَحفظ إلى preview/ و C:/Projects/.
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
    adm = admins_repo.create_admin(username="shot_super", password="x",
                                   full_name="m", is_super_admin=True)
    with transaction() as c:
        c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                  "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                  ("rtr", "r1", "10.0.0.1", "s", "mikrotik", "other", "2026-01-01"))
    aid = adm.id

from flask.sessions import SecureCookieSessionInterface
si = SecureCookieSessionInterface()
serializer = si.get_signing_serializer(app)
cookie_val = serializer.dumps({"admin_id": aid, "admin_user": "shot_super",
                               "is_super_admin": True, "tenant_id": 1})

from werkzeug.serving import make_server
PORT = 5599
srv = make_server("127.0.0.1", PORT, app, threaded=True)
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()

URL = "http://127.0.0.1:%d/admin/radius/mt/1/login-designer" % PORT
OUT = os.path.join(REPO, "preview", "addons"); os.makedirs(OUT, exist_ok=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie_val,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle", timeout=25000)
        sec = pg.query_selector(".mtld-addons-section")
        assert sec, "قسم الإضافات غير موجود"
        sec.scroll_into_view_if_needed()
        pg.wait_for_timeout(400)
        # تدقيق: كم إضافة، كم منها مفتوحة افتراضيًّا (يجب 0).
        info = pg.evaluate("""() => {
          const cards = document.querySelectorAll('.mtld-addon');
          const open = document.querySelectorAll('.mtld-addon.is-open').length;
          const acc = document.querySelectorAll('.mtld-addon-head[data-addon-acc]').length;
          const chev = document.querySelectorAll('.mtld-addon-chevron').length;
          const secH = Math.round(document.querySelector('.mtld-addons-section').getBoundingClientRect().height);
          return {cards: cards.length, openByDefault: open, accordions: acc, chevrons: chev, sectionHeightPx: secH};
        }""")
        print("AUDIT(collapsed):", info)
        sec.screenshot(path=os.path.join(OUT, "addons_collapsed_390.png"))
        sec.screenshot(path="C:/Projects/_review_addons_accordion_390.png")
        # افتح أوّل إضافة لإثبات التوسّع، وأعِد القياس.
        first = pg.query_selector(".mtld-addon-head[data-addon-acc]")
        if first:
            first.click()
            pg.wait_for_timeout(500)
            info2 = pg.evaluate("""() => ({
              open: document.querySelectorAll('.mtld-addon.is-open').length,
              firstExpanded: document.querySelector('.mtld-addon-head[data-addon-acc]').getAttribute('aria-expanded'),
              secH: Math.round(document.querySelector('.mtld-addons-section').getBoundingClientRect().height)
            })""")
            print("AUDIT(after open 1):", info2)
            sec.scroll_into_view_if_needed()
            sec.screenshot(path=os.path.join(OUT, "addons_one_open_390.png"))
            sec.screenshot(path="C:/Projects/_review_addons_accordion_open_390.png")
        b.close()
    print("done")
finally:
    srv.shutdown()
