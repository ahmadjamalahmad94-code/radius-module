# -*- coding: utf-8 -*-
"""لقطات PNG: «المتصلون الآن» (لوحة الراوتر) + الراوترات المتصلة (مركز
العمليات) — كلاهما من radacct (المصدر الموثوق). بلا API token عمداً، فما
يَظهر هو أساس radacct وحده (جوهر الإصلاح: لا فراغ زائف، آمن للنفق).

التشغيل:  python tools/capture_connected_radacct.py
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PREVIEW_DIR = os.path.join(REPO, "preview")
PORT = 5402


def _now():
    return _dt.datetime.utcnow().isoformat() + "Z"


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل الشبكة")

        def nas(name, address, vpn_peer=""):
            cur = db().execute(
                "INSERT INTO nas_devices(tenant_id,name,address,secret,vendor,"
                "enabled,connection_mode,vpn_peer_address,coa_port,created_at,updated_at) "
                "VALUES(1,?,?,'s','mikrotik',1,?,?,3799,?,?)",
                (name, address, "vpn" if vpn_peer else "direct", vpn_peer,
                 _now(), _now()))
            return int(cur.lastrowid)

        def sess(user, ip, ptype, proto=""):
            db().execute(
                "INSERT INTO radacct(tenant_id,acctsessionid,username,nasipaddress,"
                "nasporttype,framedprotocol,framedipaddress,acctstarttime,"
                "acctupdatetime,acctstoptime,acctsessiontime) "
                "VALUES(1,?,?,?,?,?,?,?,?,NULL,?)",
                (user + "-s", user, ip, ptype, proto, "100.64.0." + str(abs(hash(user)) % 200 + 2),
                 _now(), _now(), abs(hash(user)) % 9000 + 300))

        # راوتر عبر نفق واير جارد: عنوانه العام مختلف، جلساته تَصل على IP النفق.
        tunnel_id = nas("راوتر الفرع — نفق واير جارد", "41.x.public.7", vpn_peer="10.10.0.5")
        sess("ahmad", "10.10.0.5", "ethernet")          # بوابة دخول
        sess("sara", "10.10.0.5", "wireless")           # بوابة دخول
        sess("omar-fiber", "10.10.0.5", "virtual", "PPP")   # برودباند PPPoE
        sess("lina-fiber", "10.10.0.5", "ppp", "PPP")       # برودباند
        # راوتر مباشر (RADIUS-only، بلا API): جلسات على IP العام.
        nas("راوتر المركز — مباشر", "203.0.113.9")
        sess("khaled", "203.0.113.9", "ethernet")
        sess("noura", "203.0.113.9", "ppp", "PPP")
        return tunnel_id


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل الشبكة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="conn_radacct_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    tunnel_id = _seed(app)
    cookie = _cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    base = f"http://127.0.0.1:{PORT}/admin/radius"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1366, "height": 950},
                            device_scale_factor=2, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        pg.goto(base + "/mt/operations", wait_until="load", timeout=20000)
        pg.wait_for_timeout(900)
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "connected_routers_from_radacct.png"),
                      full_page=True)
        print("operations (connected routers)")
        pg.goto(base + f"/mt/{tunnel_id}/dashboard", wait_until="load", timeout=20000)
        pg.wait_for_timeout(900)
        # بطاقة «المتصلون الآن» قد تكون داخل تبويب — نلتقط الصفحة كاملة.
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "connected_now_panel_from_radacct.png"),
                      full_page=True)
        print("dashboard (المتصلون الآن)")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
