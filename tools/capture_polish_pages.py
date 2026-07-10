# -*- coding: utf-8 -*-
"""يلتقط لقطات PNG (سطح مكتب + جوّال ~390px) لصفحات لمسناها في جولة
الصقل: نظرة المشتركين العامة + نظرة المستخدمين العامة. يزرع حدًّا أدنى
من المشتركين بحالات مختلفة كي يَظهر شريط KPI الستّة وبطاقات الحالة
(وهي ما يُبرز إصلاح الالتفاف على الجوّال).

التشغيل:  python tools/capture_polish_pages.py
الناتج:    preview/<name>.png  (+ _mobile.png)
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
PORT = 5393
PAGES = [
    ("subscribers_overview_polish", "/admin/radius/subscribers/overview"),
    ("users_overview_polish", "/admin/radius/users/overview"),
    ("cards_overview_polish", "/admin/radius/cards/overview"),
]
GB = 1073741824


def _seed(app):
    """بيانات تمثيلية بأرقام «حقيقية» الحجم (عدّ ثلاثي + قيم جيجا عريضة) كي
    تَظهر صحّة الالتفاف بلا تراكب الأيقونة على الرقم ولا قصّ «0.0 GB»:
      • مشتركون عبر كل الحالات + ~120 إضافيًّا (العدّ ثلاثي، الجيجا عريضة).
      • بطاقات + دفعة لشريط KPI «نظرة الكروت»."""
    with app.app_context():
        from app.radius.db.connection import transaction
        rows = [
            ("ahmad",  "أحمد الحربي",   "0590000001", "enabled",   "Hotspot", 42, 9),
            ("sara",   "سارة العتيبي",  "0590000002", "enabled",   "PPPoE",   31, 7),
            ("khaled", "خالد القحطاني", "0590000003", "online",    "Hotspot", 27, 6),
            ("noura",  "نورة الشمري",   "0590000004", "expired",   "PPPoE",   22, 5),
            ("faisal", "فيصل الدوسري",  "0590000005", "disabled",  "Hotspot", 18, 4),
            ("huda",   "هدى المطيري",   "0590000006", "suspended", "PPPoE",   12, 3),
            ("omar",   "عمر الزهراني",  "0590000007", "enabled",   "Hotspot",  9, 2),
            ("lina",   "لينا العبدالله","0590000008", "enabled",   "PPPoE",    6, 2),
        ]
        with transaction() as c:
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(1,1,?,?,?)", ("باقة الهوت سبوت", "Hotspot", "2026-01-01"))
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(2,1,?,?,?)", ("باقة الألياف", "PPPoE", "2026-01-01"))

            def acct(user, dl, ul, active=False):
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                    "acctstarttime, acctstoptime, acctsessiontime, acctinputoctets, "
                    "acctoutputoctets) VALUES(1,?,?,?,?,?,?,?,?)",
                    (f"{user}-s1", user, "10.0.0.1", "2026-06-18 09:00:00",
                     None if active else "2026-06-18 12:00:00", 3600,
                     int(dl * GB), int(ul * GB)))

            for i, (u, n, m, st, svc, dl, ul) in enumerate(rows):
                pid = 2 if svc == "PPPoE" else 1
                # status enabled/disabled على عمود status؛ القيم الأخرى تُخزَّن
                # كما هي — الصفحة تتسامح وتعدّها ضمن «أخرى» إن لم تتطابق.
                status = "enabled" if st in ("enabled", "online") else (
                    "disabled" if st in ("disabled", "suspended") else st)
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, full_name, mobile, "
                    "plan_id, service_type, status, expire_at, created_at) "
                    "VALUES(1,?,?,?,?,?,?,?,?)",
                    (u, n, m, pid, svc, status,
                     "2026-06-25" if st == "expired" else "2026-12-31",
                     "2026-06-%02d" % (10 + i)))
                acct(u, dl, ul, active=(st == "online"))
            # ~120 مشتركًا إضافيًّا فيصير العدّ ثلاثيًّا والجيجا الإجمالية عريضة.
            for k in range(120):
                u = f"sub{k:03d}"
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, full_name, mobile, "
                    "plan_id, service_type, status, expire_at, created_at) "
                    "VALUES(1,?,?,?,1,'Hotspot','enabled','2026-12-31','2026-05-01')",
                    (u, f"مشترك {k:03d}", "059%07d" % k))
                acct(u, 3, 1)
            # بطاقات + دفعة لشريط KPI «نظرة الكروت».
            c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,created_at) "
                      "VALUES(1,'B-1',?,1,'2026-01-01')", ("حزمة الساعة",))
            bid = c.execute("SELECT id FROM card_batches WHERE tenant_id=1").fetchone()["id"]
            for k in range(340):
                c.execute("INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,created_at) "
                          "VALUES(1,?,?,'pw',1,'2026-01-01')", (bid, f"C{k:04d}"))


def _session_cookie(app) -> str:
    from flask.sessions import SecureCookieSessionInterface
    si = SecureCookieSessionInterface()
    s = si.get_signing_serializer(app)
    data = {"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
            "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"}
    return s.dumps(dict(data))


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="polish_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "shot.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
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

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.6)

    os.makedirs(PREVIEW_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 900}, device_scale_factor=2,
            locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        page = ctx.new_page()
        for name, path in PAGES:
            url = f"http://127.0.0.1:{PORT}{path}"
            page.set_viewport_size({"width": 1366, "height": 900})
            page.goto(url, wait_until="load", timeout=20000)
            page.wait_for_timeout(900)
            dp = os.path.join(PREVIEW_DIR, f"{name}.png")
            page.screenshot(path=dp, full_page=True)
            print("DESKTOP:", dp)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            mp = os.path.join(PREVIEW_DIR, f"{name}_mobile.png")
            page.screenshot(path=mp, full_page=True)
            print("MOBILE:", mp)
        browser.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
