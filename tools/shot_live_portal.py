# -*- coding: utf-8 -*-
"""رندر + لقطة لقالب «البوابة الحيّة» (live_portal) على جوّال 390px للتدقيق."""
import os, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "preview", "live_portal"); os.makedirs(OUT, exist_ok=True)

import os as _o, tempfile
_o.environ.update(HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
                  HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                  HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
from app.radius.db.connection import reset_for_tests
reset_for_tests(_o.environ["HOBERADIUS_DB_PATH"])
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    run_pending_migrations()
    from app.radius.services import hotspot_templates as ht
    # اسم محايد للمعاينة فقط (القالب يَستعمل {{TENANT_NAME}} الديناميكيّ — لا
    # علامة عيّنة مخبوزة). يَظهر الاسم الحقيقيّ للشبكة عند النشر.
    vals = {"TENANT_NAME": "Hoberadius WiFi", "ACCENT_COLOR": "#22D3EE",
            "BG_COLOR": "#0A1428", "WELCOME_TEXT": "اتصال سريع وآمن على مدار الساعة",
            "SUPPORT_PHONE": "0590000000", "MOTIF_ICON": "wifi"}
    html = ht.render("live_portal", vals, tenant_id=1)

SAMPLE = {"username": "ahmad", "ip": "10.20.0.45", "mac": "AA:BB:CC:DD:EE:FF",
          "link-login-only": "#", "link-orig": "#", "error": ""}
html = re.sub(r'\$\(if error\).*?\$\(endif\)', '', html, flags=re.S)
html = re.sub(r'\$\(([a-z0-9\-]+)\)', lambda m: SAMPLE.get(m.group(1), ''), html)
html = re.sub(r'\$\([^)]*\)', '', html)
path = os.path.join(OUT, "live_portal.html")
open(path, "w", encoding="utf-8").write(html)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844},
                        device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar")
    pg = ctx.new_page()
    pg.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=15000)
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(OUT, "live_portal_390.png"), full_page=True)
    # تدقيق: البَصمة خلفيّة + الشريط السفلي غير مُغطّى
    info = pg.evaluate(r"""()=>{
      const wm=document.querySelector('.hr-vm-pat');
      const bar=document.querySelector('.bottom-nav');
      const out={wmZ: wm?getComputedStyle(wm).zIndex:null,
                 barZ: bar?getComputedStyle(bar).zIndex:null};
      const de=document.scrollingElement||document.documentElement;
      out.bodyOverflowX=Math.max(0, de.scrollWidth-de.clientWidth);
      if(bar){const r=bar.getBoundingClientRect();out.barVisible=(r.bottom<=window.innerHeight+1&&r.height>10);}
      return out;
    }""")
    print("AUDIT:", info)
    b.close()
print("rendered:", path)
