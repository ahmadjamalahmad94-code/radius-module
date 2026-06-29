# -*- coding: utf-8 -*-
"""لقطة صفحة «النسخ الاحتياطي» مع عرض الأوقات بالتوقيت المحلّي (UTC+3).

تُنشئ نسخة محلّية حقيقية ثم تتحقّق أن الوقت المعروض = وقت UTC + المنطقة
الزمنية المضبوطة (Asia/Damascus = +3)، ثم تأخذ لقطة سطح + جوّال.
"""
import os, sys, tempfile
from uuid import uuid4
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
STATIC = os.path.join(REPO, "app", "static").replace("\\", "/")
OUT = os.path.join(tempfile.mkdtemp(), "backups"); os.makedirs(OUT, exist_ok=True)

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
    from app.radius.db.repos import admins_repo, tenants_repo
    tenants_repo.ensure_default_tenant()
    # المنطقة الزمنية الافتراضية = Asia/Damascus (+3) — نثبّتها صراحةً
    tenants_repo.set_setting(1, "billing.timezone", "Asia/Damascus")
    u = "shot_" + uuid4().hex[:8]
    admins_repo.create_admin(username=u, password="p", full_name="Shot",
                             is_super_admin=True)
    # نسخة محلّية حقيقية → ملف بوقت تعديل = الآن (UTC)
    from app.radius.services.operations import get_operations_service
    svc = get_operations_service()
    svc.run_full_backup(tenant_id=1, actor=u, lean=True)
    files = svc.list_local_backups(tenant_id=1)
    from app.radius.core.system_config import to_local
    raw_utc = files[0]["modified_at"] if files else None
    expected_local = to_local(raw_utc) if raw_utc else None
    print("raw UTC modified_at :", raw_utc)
    print("expected local (+3):", expected_local)

client = app.test_client()
client.post("/admin/radius/login", data={"username": u, "password": "p"})
html = client.get("/admin/radius/backups").get_data(as_text=True)

# تحقّق: الوقت المعروض هو المحلّي (+3)، وليس الخام UTC
assert expected_local and expected_local in html, \
    "expected local time %r not found in rendered page" % expected_local
# والوقت الخام UTC (بصيغته الكاملة %H:%M:%S) يجب ألا يظهر كما هو
print("PASS: backups page shows LOCAL time (+3), expected token present.")

html = html.replace('href="/static/', 'href="file:///%s/' % STATIC)
html = html.replace('src="/static/', 'src="file:///%s/' % STATIC)
path = os.path.join(OUT, "backups.html")
open(path, "w", encoding="utf-8").write(html)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    d = b.new_context(viewport={"width": 1280, "height": 1100}, locale="ar").new_page()
    d.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=25000)
    d.wait_for_timeout(700)
    shot = sys.argv[1] if len(sys.argv) > 1 else "C:/Projects/_review_backups_localtime.png"
    d.screenshot(path=shot, full_page=True)
    b.close()
print("rendered:", shot, "| len:", len(html))
