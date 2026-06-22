# -*- coding: utf-8 -*-
"""معاينة قبل/بعد لصفحة «إحصائيات المتصلين» — إصلاحَا التباعد + اسم الـNAS.

يُشغّل التطبيق الحقيقي على DB مؤقّتة مزروعة بجلسات radacct (طوابع ISO صحيحة
كي يُرسم الدونات) عبر عدّة أبراج: بعضها له اسم في nas_devices (يجب أن يظهر
«الاسم (IP)») وواحد بلا جهاز مطابق (يجب أن يظهر الـIP فقط)، + محاولات فاشلة
radpostauth بعنوان NAS كـIP. يلتقط لقطتين لكل نمط مطلوب:
  • جوّال حقيقي 390×844 (deviceScaleFactor 3, isMobile, hasTouch)
  • سطح مكتب 1440×900

التشغيل:
  TAG=before python tools/capture_connected_stats_fix.py   # الحالة الحاليّة
  TAG=after  python tools/capture_connected_stats_fix.py   # بعد الإصلاح
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
OUT_DIR = os.path.join(REPO, "preview", "connected_stats")
PORT = 5449
TAG = os.environ.get("TAG", "before").strip() or "before"

import datetime as _dt
TODAY = _dt.datetime.utcnow().strftime("%Y-%m-%d")


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        with transaction() as c:
            # ── أبراج لها أسماء ودّية (مطابقة على address / vpn_peer_address) ──
            devs = [
                ("برج المبنى الرئيسي", "10.10.0.2", ""),
                ("برج الفرع الشمالي", "10.10.0.3", ""),
                ("برج السكن (عبر النفق)", "10.99.0.1", "192.168.1.186"),
            ]
            for nm, addr, vpn in devs:
                c.execute(
                    "INSERT INTO nas_devices(tenant_id,name,address,vpn_peer_address,"
                    "secret,vendor,created_at) VALUES(1,?,?,?,'s','mikrotik',?)",
                    (nm, addr, vpn, TODAY + "T00:00:00Z"))

            # ── جلسات radacct (طوابع ISO …Z) عبر الأبراج + برج بلا اسم ──
            #   10.10.0.2  → مطابق (برج المبنى الرئيسي)  — كثيف
            #   10.10.0.3  → مطابق (برج الفرع الشمالي)
            #   192.168.1.186 → مطابق عبر النفق (برج السكن)
            #   10.10.0.9  → بلا جهاز → يُعرض الـIP فقط
            plan = [
                ("10.10.0.2", 9, "08"),
                ("10.10.0.3", 5, "09"),
                ("192.168.1.186", 3, "10"),
                ("10.10.0.9", 2, "11"),
            ]
            uid = 0
            for ip, n, hh in plan:
                for i in range(n):
                    uid += 1
                    c.execute(
                        "INSERT INTO radacct(tenant_id,username,nasipaddress,"
                        "callingstationid,framedipaddress,acctstarttime,"
                        "acctsessiontime,acctinputoctets,acctoutputoctets) "
                        "VALUES(1,?,?,?,?,?,?,?,?)",
                        (f"user{uid:03d}", ip, f"AA:BB:CC:{uid:02d}:11:22",
                         f"10.20.0.{uid}", f"{TODAY}T{hh}:{i:02d}:00Z",
                         600 + i * 30, 1000000 * uid, 500000 * uid))

            # ── محاولات فاشلة radpostauth (عنوان NAS كـIP) ──
            for i in range(4):
                c.execute(
                    "INSERT INTO radpostauth(tenant_id,username,reply,authdate,nas) "
                    "VALUES(1,?,?,?,?)",
                    (f"bad{i}", "Access-Reject", f"{TODAY} 12:0{i}:00",
                     "10.10.0.2" if i % 2 == 0 else "10.10.0.9"))


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


# (label, path) للصفحات المطلوبة
PAGES = [
    ("stats_unique", "/admin/radius/connected-stats?mode=unique"),
    ("stats_failed", "/admin/radius/connected-stats?mode=failed"),
    ("online", "/admin/radius/online"),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="cstats_fix_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
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
    cookie = _cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)
    os.makedirs(OUT_DIR, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        mob = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=3, is_mobile=True, has_touch=True,
                            locale="ar")
        mob.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        desk = b.new_context(viewport={"width": 1440, "height": 900},
                             device_scale_factor=1, locale="ar")
        desk.add_cookies([{"name": "session", "value": cookie,
                           "domain": "127.0.0.1", "path": "/"}])
        mp = mob.new_page()
        dp = desk.new_page()
        for name, path in PAGES:
            url = f"http://127.0.0.1:{PORT}{path}"
            try:
                r = mp.goto(url, wait_until="load", timeout=25000)
                mp.wait_for_timeout(900)
                mp.screenshot(path=os.path.join(OUT_DIR, f"{TAG}_{name}_390.png"),
                              full_page=True)
                print(f"{name:16s} 390  status={r.status if r else '?'}")
            except Exception as e:  # noqa: BLE001
                print(f"{name} 390 ERROR {e}")
            try:
                dp.goto(url, wait_until="load", timeout=25000)
                dp.wait_for_timeout(700)
                dp.screenshot(path=os.path.join(OUT_DIR, f"{TAG}_{name}_1440.png"),
                              full_page=True)
            except Exception as e:  # noqa: BLE001
                print(f"{name} 1440 ERROR {e}")
        b.close()
    srv.shutdown()
    print(f"\nSaved → {OUT_DIR}  (prefix {TAG}_)")


if __name__ == "__main__":
    main()
