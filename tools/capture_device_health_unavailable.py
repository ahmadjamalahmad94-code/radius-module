# -*- coding: utf-8 -*-
"""لقطة PNG: صفحة «تتبع حالة الأجهزة» بعد الإصلاح — جهاز «test» خلف راوتر ccr3
المفصول يَظهر «غير متاح — الراوتر مفصول» (أحمر)، وبطاقة KPI «غير متاحة = 1»،
فلا تبدو اللوحة «سليمة». جهاز ثانٍ سليم للتباين.

التشغيل:  python tools/capture_device_health_unavailable.py
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
PORT = 5421


def _seed(app):
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import (admins_repo, tenants_repo, nas_repo,
                                          device_health_repo as repo)
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل الشبكة", is_super_admin=True)
        ccr3 = nas_repo.upsert_nas(NasDevice(
            id=None, name="ccr3", address="192.168.15.1", secret="s",
            vendor="mikrotik", enabled=True))
        # الجهاز المتأثّر: خلف ccr3 المفصول ⇒ «غير متاح».
        d1 = repo.create_device(
            tenant_id=1, router_id=int(ccr3.id), name="test",
            interface_name="", ip_address="192.168.15.10",
            network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
            device_type="access_point", notes="كاميرا المدخل")
        repo.set_status(tenant_id=1, device_id=d1, status="unavailable")
        # جهاز سليم للتباين.
        d2 = repo.create_device(
            tenant_id=1, router_id=int(ccr3.id), name="أكسس بوينت الاستقبال",
            interface_name="", ip_address="192.168.15.20",
            network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
            device_type="access_point")
        repo.set_status(tenant_id=1, device_id=d2, status="up", latency_ms=6.0)
        # دورة فحص في السجل: «غير متاح» مطويّ في «المفصول» ⇒ ليست «سليمة».
        from app.radius.db.repos import device_health_checks_repo as checks
        checks.insert_check(
            tenant_id=1, source="poller", ok=True,
            summary={"scanned": 2, "up": 1, "down": 0, "unavailable": 1,
                     "high_latency": 0, "unknown": 0, "changed": 1, "alerts": 1},
            duration_ms=1840,
            details=[{"device_id": d1, "name": "test", "status": "unavailable",
                      "latency_ms": None},
                     {"device_id": d2, "name": "أكسس بوينت الاستقبال",
                      "status": "up", "latency_ms": 6.0}])


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل الشبكة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dh_unavail_shot_")
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
    url = f"http://127.0.0.1:{PORT}/admin/radius/device-health"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        for label, w in (("desktop", 1366), ("mobile", 412)):
            ctx = b.new_context(viewport={"width": w, "height": 1100},
                                device_scale_factor=2, locale="ar")
            ctx.add_cookies([{"name": "session", "value": cookie,
                              "domain": "127.0.0.1", "path": "/"}])
            pg = ctx.new_page()
            pg.goto(url, wait_until="load", timeout=20000)
            pg.wait_for_timeout(900)
            pg.screenshot(path=os.path.join(PREVIEW_DIR, f"device_health_unavailable_{label}.png"),
                          full_page=True)
            print(f"captured {label}")
            ctx.close()
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
