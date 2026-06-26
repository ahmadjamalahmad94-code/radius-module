# -*- coding: utf-8 -*-
"""رندر + لقطة لقالب «قهوة الصباح» (morning_coffee) على جوّال 390px وسطح 1280px."""
import os, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "preview", "morning_coffee"); os.makedirs(OUT, exist_ok=True)

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
    vals = {"TENANT_NAME": "Hoberadius WiFi", "ACCENT_COLOR": "#A8612F",
            "BG_COLOR": "#FBEFE2", "WELCOME_TEXT": "قهوة طازجة وإنترنت سريع — صباح أجمل",
            "SUPPORT_PHONE": "0590000000", "MOTIF_ICON": "coffee"}
    html = ht.render("morning_coffee", vals, tenant_id=1)

SAMPLE = {"username": "ahmad", "ip": "10.20.0.45", "mac": "AA:BB:CC:DD:EE:FF",
          "link-login-only": "#", "link-orig": "#", "error": ""}
html = re.sub(r'\$\(if error\).*?\$\(endif\)', '', html, flags=re.S)
html = re.sub(r'\$\(([a-z0-9\-]+)\)', lambda m: SAMPLE.get(m.group(1), ''), html)
html = re.sub(r'\$\([^)]*\)', '', html)
path = os.path.join(OUT, "morning_coffee.html")
open(path, "w", encoding="utf-8").write(html)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    # ── جوّال 390px ──
    ctx = b.new_context(viewport={"width": 390, "height": 844},
                        device_scale_factor=2, is_mobile=True, has_touch=True, locale="ar")
    pg = ctx.new_page()
    pg.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=15000)
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(OUT, "morning_coffee_390.png"), full_page=True)
    pg.screenshot(path="C:/Projects/_review_morning_coffee_390.png", full_page=True)
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
    print("AUDIT(mobile):", info)
    ctx.close()
    # ── سطح المكتب ~1280px ──
    ctx2 = b.new_context(viewport={"width": 1280, "height": 900},
                         device_scale_factor=1, locale="ar")
    pg2 = ctx2.new_page()
    pg2.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=15000)
    pg2.wait_for_timeout(700)
    pg2.screenshot(path=os.path.join(OUT, "morning_coffee_desktop.png"), full_page=True)
    pg2.screenshot(path="C:/Projects/_review_morning_coffee_desktop.png", full_page=True)
    b.close()
print("rendered:", path)
