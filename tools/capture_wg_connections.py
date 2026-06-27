# -*- coding: utf-8 -*-
"""لقطتا صفحة «اتصالات WireGuard» (/admin/radius/mt/wg-peers) — جوّال 390 +
سطح مكتب — للمراجعة. يزرع راوترات WG بحالات متنوّعة + ملفات peer على الخادم
+ إعدادات خادم WG في البيئة كي تَظهر الـKPIs والجدول وكشف المفاتيح والحالات.

التشغيل:  python tools/capture_wg_connections.py
الناتج :  C:/Projects/_review_wg_connections_390.png  +  _desktop.png
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
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 5398
PAGE = "/admin/radius/mt/wg-peers"

# راوترات WG: (اسم، tunnel_ip، pubkey، iface، enabled، last_check، has_peer_file)
ROUTERS = [
    ("ccr5-amman",   "10.10.0.2", "kP8aQ2x7Vn1mWqZ4Lr9TbY6cHj0Es3dGf5uIo2pA1Q=", "hr-wg", 1, "reachable",   True),
    ("ccr4-irbid",   "10.10.0.3", "9Lm2Nv5Bx8Cq1Zr4Ty7Uw0Ei3Op6As9Df2Gh5Jk8L0=", "hr-wg", 1, "reachable",   True),
    ("hex-zarqa",    "10.10.0.4", "Rt6Yu9Io2Pa5Sd8Fg1Hj4Kl7Zx0Cv3Bn6Mq9Wd2Er5=", "hr-wg", 1, "timeout",     True),
    ("rb5009-aqaba", "10.10.0.5", "Qa1Ws2Ed3Rf4Tg5Yh6Uj7Ik8Ol9Pz0Xc1Vb2Nm3Lk4=", "hr-wg", 1, "",            False),
    ("ccr2-mafraq",  "10.10.0.6", "Zm9Xn8Cb7Vd6Fg5Hj4Kl3Pq2Wr1Es0Ty9Ui8Op7As6=", "hr-wg", 0, "",            True),
]


def _seed(app, peers_dir: str):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            for i, (name, ip, pub, iface, en, chk, _hp) in enumerate(ROUTERS):
                c.execute(
                    "INSERT INTO nas_devices(tenant_id,name,address,secret,vendor,"
                    " nas_type,enabled,connection_mode,vpn_peer_address,"
                    " vpn_public_key,vpn_interface,ros_version,last_check_status,"
                    " last_check_at,created_at) VALUES "
                    "(1,?,?,?,'mikrotik','hotspot',?,'vpn',?,?,?,'7',?,?,?)",
                    (name, ip, "s%d" % i, en, ip, pub, iface, chk,
                     "2026-06-27T08:30:00Z" if chk else "",
                     "2026-06-%02dT09:00:00Z" % (10 + i)))
    # ملفات peer على الخادم (لتُحَلّ «Peer على الخادم = موجود/غير موجود»)
    os.makedirs(peers_dir, exist_ok=True)
    for name, ip, pub, _i, _e, _c, has_peer in ROUTERS:
        if not has_peer:
            continue
        with open(os.path.join(peers_dir, name + ".conf"), "w", encoding="utf-8") as f:
            f.write("[Peer]\nPublicKey = %s\nAllowedIPs = %s/32\n" % (pub, ip))


def _session_cookie(app) -> str:
    from flask.sessions import SecureCookieSessionInterface
    si = SecureCookieSessionInterface()
    s = si.get_signing_serializer(app)
    data = {"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
            "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"}
    return s.dumps(dict(data))


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="wgconn_shot_")
    peers_dir = os.path.join(tmp, "wg-peers.d")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "shot.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
        FLASK_SECRET="preview-secret-key",
        HOBERADIUS_WG_PEERS_DIR=peers_dir,
        HOBERADIUS_WG_SUBNET="10.10.0.0/24",
        HOBERADIUS_WG_SERVER_IP="10.10.0.1",
        HOBERADIUS_WG_SERVER_PUBKEY="SrV9Pub8Key7Aa6Bb5Cc4Dd3Ee2Ff1Gg0Hh9Ii8Jj7=",
        HOBERADIUS_WG_SERVER_ENDPOINT="vpn.hoberadius.net:13231",
    )
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    _seed(app, peers_dir)
    cookie = _session_cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.6)

    from playwright.sync_api import sync_playwright
    url = f"http://127.0.0.1:{PORT}{PAGE}"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1366, "height": 900},
                                  device_scale_factor=2, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=20000)
        page.wait_for_timeout(900)
        page.screenshot(path="C:/Projects/_review_wg_connections_desktop.png",
                        full_page=True)
        print("DESKTOP done")
        # تدقيق التجاوز الأفقيّ على الجوّال
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(600)
        overflow = page.evaluate(
            "()=>{const e=document.scrollingElement||document.documentElement;"
            "return Math.max(0,e.scrollWidth-e.clientWidth);}")
        print("MOBILE body overflow-x px:", overflow)
        page.screenshot(path="C:/Projects/_review_wg_connections_390.png",
                        full_page=True)
        print("MOBILE done")
        browser.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
