# -*- coding: utf-8 -*-
"""لقطات PNG: (1) صفحة تتبّع حالة الأجهزة مع شريط «تلجرام غير مُفعّل»،
(2) مركز الإشعارات يعرض إشعار انقطاع/عودة جهاز (التسطيح داخل اللوحة).

يشغّل اللوحة على DB مؤقّتة مزروعة (مشغّل + راوتر + جهازان مُراقَبان + حدث
انقطاع وعودة)، كوكي سوبر، ثم Playwright. تلجرام غير مُفعّل عمدًا ليظهر
الشريط ويحمل إشعار الجرس تلميح التفعيل.

التشغيل:  python tools/capture_device_health_alert.py
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
PORT = 5399


def _seed(app):
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import (admins_repo, tenants_repo, nas_repo,
                                          device_health_repo as dh)
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل الشبكة")
        nas = nas_repo.upsert_nas(NasDevice(
            id=None, name="راوتر المبنى الرئيسي", address="10.0.0.1", secret="s",
            vendor="mikrotik", enabled=True))
        rid = int(nas.id)
        # جهازان مُراقَبان: واحد سيُسجَّل «down» والآخر «up».
        d_down = dh.create_device(
            tenant_id=1, router_id=rid, name="أكسس بوينت السطح",
            interface_name="", ip_address="192.168.88.50",
            network_cidr="192.168.88.0/24", gateway_address="192.168.88.1",
            device_type="access_point")
        dh.create_device(
            tenant_id=1, router_id=rid, name="أكسس بوينت الطابق 2",
            interface_name="", ip_address="192.168.88.51",
            network_cidr="192.168.88.0/24", gateway_address="192.168.88.1",
            device_type="access_point")
        # عيّنتان down ⇒ تجاوز العتبة، ثم أطلق التنبيه (تلجرام غير مُفعّل).
        dh.set_status(tenant_id=1, device_id=d_down, status="down")
        dh.set_status(tenant_id=1, device_id=d_down, status="down")
        from app.radius.services import device_health_alerts as dha
        fresh = dh.get_device(1, d_down)
        dha.evaluate_and_dispatch(tenant_id=1, device=fresh, prev_status="up",
                                  new_status="down", latency_ms=None)
        # عودة الجهاز.
        dh.set_status(tenant_id=1, device_id=d_down, status="up")
        fresh = dh.get_device(1, d_down)
        dha.evaluate_and_dispatch(tenant_id=1, device=fresh, prev_status="down",
                                  new_status="up", latency_ms=14.0)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل الشبكة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dh_alert_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    _seed(app)
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
        pg.goto(base + "/device-health", wait_until="load", timeout=20000)
        pg.wait_for_timeout(900)
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "device_health_telegram_banner.png"),
                      full_page=True)
        print("device-health banner")
        pg.goto(base + "/notifications", wait_until="load", timeout=20000)
        pg.wait_for_timeout(800)
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "device_health_panel_notifications.png"),
                      full_page=True)
        print("notifications center")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
