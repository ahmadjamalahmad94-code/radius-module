# -*- coding: utf-8 -*-
"""رندر + لقطتا «النيون الداكن» (neon_dark) — جوّال 390 + سطح مكتب 1280."""
import os, re, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "preview", "neon_dark"); os.makedirs(OUT, exist_ok=True)
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
    from app.radius.services import hotspot_templates as ht
    # اسم محايد — القالب ديناميكيّ ({{TENANT_NAME}})، لا علامة عيّنة مخبوزة.
    vals = {"TENANT_NAME": "Hoberadius WiFi", "ACCENT_COLOR": "#4ADE80",
            "BG_COLOR": "#050B08", "WELCOME_TEXT": "اتصال سريع وآمن على مدار الساعة",
            "SUPPORT_PHONE": "0590000000", "MOTIF_ICON": "wifi"}
    html = ht.render("neon_dark", vals, tenant_id=1)
SAMPLE = {"username": "ahmad", "ip": "10.20.0.45", "mac": "AA:BB:CC:DD:EE:FF",
          "link-login-only": "#", "link-orig": "#", "error": ""}
html = re.sub(r'\$\(if error\).*?\$\(endif\)', '', html, flags=re.S)
html = re.sub(r'\$\(([a-z0-9\-]+)\)', lambda m: SAMPLE.get(m.group(1), ''), html)
html = re.sub(r'\$\([^)]*\)', '', html)
path = os.path.join(OUT, "neon_dark.html"); open(path, "w", encoding="utf-8").write(html)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    # جوّال
    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                      is_mobile=True, has_touch=True, locale="ar")
    mp = m.new_page(); mp.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=15000)
    mp.wait_for_timeout(700)
    mp.screenshot(path=os.path.join(OUT, "neon_dark_390.png"), full_page=True)
    mp.screenshot(path="C:/Projects/_review_neon_dark_390.png", full_page=True)
    info = mp.evaluate(r"""()=>{const wm=document.querySelector('.hr-vm-pat');const bar=document.querySelector('.bottom-nav');
      const de=document.scrollingElement||document.documentElement;const out={wmZ:wm?getComputedStyle(wm).zIndex:null,barZ:bar?getComputedStyle(bar).zIndex:null,bodyOverflowX:Math.max(0,de.scrollWidth-de.clientWidth)};
      if(bar){const r=bar.getBoundingClientRect();out.barVisible=(r.bottom<=window.innerHeight+1&&r.height>10);}return out;}""")
    print("AUDIT:", info)
    # سطح مكتب
    d = b.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1, locale="ar")
    dp = d.new_page(); dp.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=15000)
    dp.wait_for_timeout(700)
    dp.screenshot(path="C:/Projects/_review_neon_dark_desktop.png", full_page=True)
    b.close()
print("done")
