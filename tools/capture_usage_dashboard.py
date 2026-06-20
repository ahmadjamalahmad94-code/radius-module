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
    """مشتركون متنوّعو الاستهلاك (KPI/دائري/أعلى-10/جدول مملوء)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        # أسماء + استهلاك (تنزيل GB، رفع GB) — متدرّجة لرسم بياني واضح.
        people = [
            ("ahmad", "أحمد الحربي", "0590000001", "الباقة الذهبية", 42, 9),
            ("sara", "سارة العتيبي", "0590000002", "الباقة الفضية", 31, 7),
            ("khaled", "خالد القحطاني", "0590000003", "الباقة الذهبية", 27, 6),
            ("noura", "نورة الشمري", "0590000004", "باقة الألياف", 22, 5),
            ("faisal", "فيصل الدوسري", "0590000005", "الباقة الفضية", 18, 4),
            ("huda", "هدى المطيري", "0590000006", "باقة الألياف", 15, 3),
            ("omar", "عمر الزهراني", "0590000007", "الباقة الذهبية", 12, 3),
            ("layla", "ليلى الغامدي", "0590000008", "الباقة البرونزية", 9, 2),
            ("majed", "ماجد السبيعي", "0590000009", "الباقة الفضية", 7, 2),
            ("rana", "رنا العنزي", "0590000010", "الباقة البرونزية", 5, 1),
            ("yousef", "يوسف الرشيدي", "0590000011", "باقة الألياف", 3, 1),
            ("dana", "دانة الحارثي", "0590000012", "الباقة البرونزية", 2, 1),
        ]
        plans = {}
        with transaction() as c:
            for _u, _n, _m, plan, _d, _ul in people:
                if plan not in plans:
                    c.execute("INSERT INTO access_plans(tenant_id, name, created_at) "
                              "VALUES(1,?,?)", (plan, "2026-01-01"))
                    plans[plan] = c.execute(
                        "SELECT id FROM access_plans WHERE tenant_id=1 AND name=?",
                        (plan,)).fetchone()["id"]
            for i, (u, n, m, plan, dl, ul) in enumerate(people):
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, full_name, mobile, "
                    "plan_id, status, created_at) VALUES(1,?,?,?,?,'enabled','2026-01-01')",
                    (u, n, m, plans[plan]))
                # جلستان: واحدة نشطة لأول 4 (للعدّاد «متصل الآن»).
                active = None if i < 4 else "2026-06-10 12:00:00"
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                    "acctstarttime, acctstoptime, acctsessiontime, acctinputoctets, "
                    "acctoutputoctets) VALUES(1,?,?,?,?,?,?,?,?)",
                    (f"{u}-s1", u, "10.0.0.1", "2026-06-10 09:00:00", active,
                     3600, int(dl * GB), int(ul * GB)))


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
