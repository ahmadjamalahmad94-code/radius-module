# -*- coding: utf-8 -*-
"""لقطة لوح «③ الإضافات»: المجموعات الخمس مطويّة افتراضيًّا + واحدة موسَّعة.
يسجّل دخولًا عبر test client، يجلب صفحة المصمّم الحقيقيّة، يعيد توجيه روابط
static إلى file://، يوسّع مجموعة واحدة، ثم يلتقط لوح الإضافات."""
import os, re, sys, tempfile
from uuid import uuid4
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
STATIC = os.path.join(REPO, "app", "static").replace("\\", "/")
OUT = os.path.join(REPO, "preview", "addon_groups"); os.makedirs(OUT, exist_ok=True)

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
    now = datetime.utcnow().isoformat() + "Z"
    with transaction() as c:
        c.execute("""INSERT INTO nas_devices (id, tenant_id, name, address, secret,
                     vendor, nas_type, enabled, created_at, connection_mode)
                     VALUES (1,1,'r-rtr','203.0.113.9','sek','mikrotik','hotspot',1,?,'direct')""",
                  (now,))
    from app.radius.db.repos import admins_repo
    u = "shot_" + uuid4().hex[:8]
    admins_repo.create_admin(username=u, password="p", full_name="Shot",
                             is_super_admin=True)

client = app.test_client()
client.post("/admin/radius/login", data={"username": u, "password": "p"})
html = client.get("/admin/radius/mt/1/login-designer").get_data(as_text=True)

# روابط static المطلقة → file:// كي تُحمّل CSS محليًّا في playwright.
html = html.replace('href="/static/', 'href="file:///%s/' % STATIC)
html = html.replace('src="/static/', 'src="file:///%s/' % STATIC)

path = os.path.join(OUT, "designer.html")
open(path, "w", encoding="utf-8").write(html)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_context(viewport={"width": 980, "height": 900}, locale="ar").new_page()
    pg.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=25000)
    pg.wait_for_timeout(700)
    # وسّع المجموعة الأولى لإظهار «مطويّة + موسَّعة» في صورة واحدة.
    info = pg.evaluate(r"""()=>{
      var grps=[].slice.call(document.querySelectorAll('[data-addon-grp]'));
      // وسّع مجموعة «الربح والتسويق» (الأصغر) كي تظهر رؤوس المجموعات الخمس
      // كلّها (٤ مطويّة + ١ موسَّعة) في صورة واحدة دون طول مفرط.
      var idx = Math.min(3, grps.length-1);
      if(grps.length){
        var h=grps[idx].querySelector('[data-addon-grp-acc]'); if(h) h.click();
      }
      var panel=document.querySelector('[data-mtld-addons]');
      if(panel) panel.scrollIntoView();
      return {groups: grps.length,
              openBodies: document.querySelectorAll('.mtld-addon-cat.is-open').length};
    }""")
    print("AUDIT:", info)
    pg.wait_for_timeout(500)
    panel = pg.query_selector('[data-mtld-addons]')
    if panel:
        panel.screenshot(path="C:/Projects/_review_addon_groups_collapsed.png")
    else:
        pg.screenshot(path="C:/Projects/_review_addon_groups_collapsed.png", full_page=True)
    b.close()
print("rendered:", path)
