# -*- coding: utf-8 -*-
"""لقطة صفحة «دعم المتجر المتقدّم» — جوّال + سطح مكتب (مراجعة التباين/الوضوح).

يُشغّل الخادم، يَحقن جلسة super-admin، يَبذُر قنوات استلام + طلبات إيداع/سحب
عَيّنة (SQL مباشر) لإظهار الجداول، ثم يُصوّر الصفحة. وسيطة اختياريّة للمخرَج.
"""
import os, sys, tempfile, threading
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""
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
    adm = admins_repo.create_admin(username="ss_super", password="x",
                                   full_name="m", is_super_admin=True)
    aid = adm.id
    from app.radius.services.store_deposits import DepositRequestService
    dsvc = DepositRequestService(tenant_id=1)
    dsvc.create_payment_method(method="jawaly_pay", label="محفظة جوالي باي",
                               account_name="متجر هوبي", account_number="0599123456",
                               instructions="حوّل ثم ارفع صورة الوصل.", sort_order=0)
    dsvc.create_payment_method(method="bank", label="بنك فلسطين",
                               account_name="Hobe Store", account_number="PS00 1234 5678",
                               instructions="تحويل بنكي داخليّ.", sort_order=1)
    # طلبات إيداع/سحب عَيّنة عبر SQL مباشر لإظهار الجداول.
    now = "2026-06-27T10:00:00Z"
    with transaction() as c:
        c.execute("INSERT INTO card_users(tenant_id,mobile,display_name,created_at) "
                  "VALUES(1,?,?,?)", ("0591000000", "أحمد العميل", now))
        cu = c.execute("SELECT id FROM card_users WHERE mobile='0591000000'").fetchone()[0]
        for nm, ph, amt, st in [("أحمد العميل", "0591000000", 5000, "pending"),
                                 ("سارة محمد", "0592000000", 12000, "pending"),
                                 ("خالد يوسف", "0593000000", 8000, "confirmed")]:
            c.execute("INSERT INTO deposit_requests(tenant_id,card_user_id,method,"
                      "payer_phone,payer_name,amount_claimed_minor,status,currency,"
                      "created_at) VALUES(1,?,?,?,?,?,?,?,?)",
                      (cu, "jawaly_pay", ph, nm, amt, st, "ILS", now))
        for nm, acc, amt, st in [("أحمد العميل", "PS11 2222 3333", 3000, "pending"),
                                  ("سارة محمد", "0592000000", 6000, "confirmed")]:
            c.execute("INSERT INTO withdrawal_requests(tenant_id,card_user_id,"
                      "payee_name,payee_account,amount_minor,status,currency,created_at) "
                      "VALUES(1,?,?,?,?,?,?,?)", (cu, nm, acc, amt, st, "ILS", now))

from flask.sessions import SecureCookieSessionInterface
serializer = SecureCookieSessionInterface().get_signing_serializer(app)
cookie_val = serializer.dumps({"admin_id": aid, "admin_user": "ss_super",
                               "is_super_admin": True, "tenant_id": 1})
from werkzeug.serving import make_server
PORT = 5603
srv = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/admin/radius/store-support" % PORT
OUT = os.path.join(REPO, "preview", "store_support"); os.makedirs(OUT, exist_ok=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        # جوّال
        m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                          is_mobile=True, has_touch=True, locale="ar")
        m.add_cookies([{"name": "session", "value": cookie_val, "domain": "127.0.0.1", "path": "/"}])
        mp = m.new_page(); mp.goto(URL, wait_until="networkidle", timeout=25000)
        mp.wait_for_timeout(500)
        mp.screenshot(path="C:/Projects/_review_store_support%s_390.png" % SUFFIX, full_page=True)
        # سطح مكتب — لقطة الإيداع + لقطة المحافظ
        d = b.new_context(viewport={"width": 1280, "height": 1000}, device_scale_factor=1, locale="ar")
        d.add_cookies([{"name": "session", "value": cookie_val, "domain": "127.0.0.1", "path": "/"}])
        dp = d.new_page(); dp.goto(URL, wait_until="networkidle", timeout=25000)
        dp.wait_for_timeout(500)
        dp.screenshot(path="C:/Projects/_review_store_support%s_desktop.png" % SUFFIX, full_page=True)
        dp.click("[data-ssp-tab='wallets']"); dp.wait_for_timeout(500)
        dp.screenshot(path=os.path.join(OUT, "wallets%s_desktop.png" % SUFFIX), full_page=True)
        b.close()
    print("done")
finally:
    srv.shutdown()
