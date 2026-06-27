# -*- coding: utf-8 -*-
"""لقطة صفحة إعدادات النظام (المُعاد تصميمها) — جوّال 390 + سطح 1280."""
import os, sys, tempfile
from uuid import uuid4
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
STATIC = os.path.join(REPO, "app", "static").replace("\\", "/")
OUT = os.path.join(REPO, "preview", "settings_system"); os.makedirs(OUT, exist_ok=True)

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
    from app.radius.db.repos import admins_repo
    u = "shot_" + uuid4().hex[:8]
    admins_repo.create_admin(username=u, password="p", full_name="Shot",
                             is_super_admin=True)

client = app.test_client()
client.post("/admin/radius/login", data={"username": u, "password": "p"})
html = client.get("/admin/radius/settings").get_data(as_text=True)
html = html.replace('href="/static/', 'href="file:///%s/' % STATIC)
html = html.replace('src="/static/', 'src="file:///%s/' % STATIC)
path = os.path.join(OUT, "settings.html")
open(path, "w", encoding="utf-8").write(html)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    # سطح المكتب
    d = b.new_context(viewport={"width": 1280, "height": 1000}, locale="ar").new_page()
    d.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=25000)
    d.wait_for_timeout(800)
    d.screenshot(path="C:/Projects/_review_settings_system_desktop.png", full_page=True)
    # جوّال 390
    m = b.new_context(viewport={"width": 390, "height": 844},
                      device_scale_factor=2, is_mobile=True, locale="ar").new_page()
    m.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=25000)
    m.wait_for_timeout(800)
    m.screenshot(path="C:/Projects/_review_settings_system_390.png", full_page=True)
    b.close()
print("rendered:", path, "| len:", len(html))
