# -*- coding: utf-8 -*-
"""يلتقط لقطة PNG لتقرير استهلاك المشتركين ببيانات عيّنة تمثيلية.

يشغّل التطبيق محليًّا على DB مؤقّتة مزروعة، يصنع كوكي جلسة موقّعة لمدير
رئيسي (فلا حاجة لتسجيل دخول تفاعلي)، ثم يلتقط الصفحة عبر Playwright
(متصفّح النظام Chrome) بعرض سطح المكتب وبعرض الجوّال ~390px.

التشغيل:  python tools/capture_usage_dashboard.py
الناتج:    preview/subscriber_usage_dashboard.png  (+ _mobile.png)
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PREVIEW_DIR = os.path.join(REPO, "preview")
PORT = 5391
PATH = "/admin/radius/reports/subscriber-consumption"
GB = 1073741824


def _seed(app):
    """عيّنة مختلطة من كل الأنواع لإظهار التبويبات + شارات النوع:
      مشتركون هوت سبوت + برودباند (PPPoE) + بطاقات (أرقام، جدول cards)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        # (username, full_name, mobile, service_type, دل, رفع) للمشتركين.
        subs = [
            ("ahmad", "أحمد الحربي", "0590000001", "Hotspot", 42, 9),
            ("sara", "سارة العتيبي", "0590000002", "PPPoE", 31, 7),
            ("khaled", "خالد القحطاني", "0590000003", "Hotspot", 27, 6),
            ("noura", "نورة الشمري", "0590000004", "PPPoE", 22, 5),
            ("faisal", "فيصل الدوسري", "0590000005", "Hotspot", 18, 4),
            ("huda", "هدى المطيري", "0590000006", "PPPoE", 12, 3),
            ("omar", "عمر الزهراني", "0590000007", "Hotspot", 9, 2),
        ]
        # بطاقات هوت سبوت (أرقام، بلا اسم → تُعرض باسم الحزمة).
        cards = [("7772", 24, 5), ("10", 16, 4), ("33", 11, 3),
                 ("3123", 6, 2), ("2044", 4, 1)]
        with transaction() as c:
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(1,1,?,?,?)", ("باقة الهوت سبوت", "Hotspot", "2026-01-01"))
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(2,1,?,?,?)", ("باقة الألياف", "PPPoE", "2026-01-01"))
            c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,created_at) "
                      "VALUES(1,'B-1',?,1,'2026-01-01')", ("حزمة الساعة",))
            bid = c.execute("SELECT id FROM card_batches WHERE tenant_id=1").fetchone()["id"]

            def acct(user, dl, ul, active=False):
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                    "acctstarttime, acctstoptime, acctsessiontime, acctinputoctets, "
                    "acctoutputoctets) VALUES(1,?,?,?,?,?,?,?,?)",
                    (f"{user}-s1", user, "10.0.0.1", "2026-06-10 09:00:00",
                     None if active else "2026-06-10 12:00:00", 3600,
                     int(dl * GB), int(ul * GB)))
            for i, (u, n, m, st, dl, ul) in enumerate(subs):
                pid = 2 if st == "PPPoE" else 1
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, full_name, mobile, "
                    "plan_id, service_type, status, created_at) "
                    "VALUES(1,?,?,?,?,?,'enabled','2026-01-01')", (u, n, m, pid, st))
                acct(u, dl, ul, active=(i < 3))  # أوّل 3 نشطون
            for u, dl, ul in cards:
                c.execute("INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,created_at) "
                          "VALUES(1,?,?,'pw',1,'2026-01-01')", (bid, u))
                acct(u, dl, ul)


def _session_cookie(app) -> str:
    from flask.sessions import SecureCookieSessionInterface
    si = SecureCookieSessionInterface()
    s = si.get_signing_serializer(app)
    data = {"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
            "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"}
    return s.dumps(dict(data))


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="usage_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "shot.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      # تجاوز بوّابة الترخيص للمعاينة فقط (يتطلب NO_SEED معه)
                      # — نفس ما تفعله بيئة الاختبار، فلا تُعاد توجيه الصفحة.
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    _seed(app)
    cookie = _session_cookie(app)

    from werkzeug.serving import make_server  # خادم متعدّد الخيوط
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.6)

    os.makedirs(PREVIEW_DIR, exist_ok=True)
    desktop_png = os.path.join(PREVIEW_DIR, "subscriber_usage_dashboard.png")
    mobile_png = os.path.join(PREVIEW_DIR, "subscriber_usage_dashboard_mobile.png")
    url = f"http://127.0.0.1:{PORT}{PATH}"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 900}, device_scale_factor=2,
            locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=20000)
        page.wait_for_timeout(1200)  # uds_table يبني شريط الأدوات/الترقيم
        page.screenshot(path=desktop_png, full_page=True)
        print("DESKTOP:", desktop_png)

        # جوّال ~390px.
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        page.screenshot(path=mobile_png, full_page=True)
        print("MOBILE:", mobile_png)
        browser.close()

    srv.shutdown()


if __name__ == "__main__":
    main()
