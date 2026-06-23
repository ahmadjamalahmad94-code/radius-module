# -*- coding: utf-8 -*-
"""لقطة PNG: صفحة إعدادات تنبيهات المراقبة مع قسم «التنبيهات الدوريّة» الجديد
(تذكير المفصول + تقرير الأسطول)، وطباعة نصوص رسائل نموذجيّة (تقرير سليم / تقرير
بملاحظات / تذكير) إلى preview/periodic_messages.txt.

التشغيل:  python tools/capture_periodic_notifications.py
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
PORT = 5423


def _seed(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل الشبكة", is_super_admin=True)


def _sample_messages(app) -> str:
    """يَبني نصوص رسائل نموذجيّة من المحرّك الحقيقي (لا قوالب يدويّة)."""
    out: list[str] = []
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import (nas_repo, device_health_repo as dh,
                                          router_resource_repo as rr)
        from app.radius.db.connection import db
        from app.radius.services import monitoring_digest as md
        # سيناريو «كل شيء سليم».
        ok_r = nas_repo.upsert_nas(NasDevice(id=None, name="rb-main", address="10.0.0.1",
                                             secret="s", vendor="mikrotik", enabled=True))
        db().execute("UPDATE nas_devices SET last_check_status='reachable' WHERE id=?", (int(ok_r.id),))
        d_ok = dh.create_device(tenant_id=1, router_id=int(ok_r.id), name="كاميرا المدخل",
                                interface_name="", ip_address="192.168.15.20",
                                network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
                                device_type="access_point")
        dh.set_status(tenant_id=1, device_id=d_ok, status="up")
        db().commit()
        out.append("── تقرير دوريّ: كل شيء سليم ──")
        out.append(md.build_digest_message(md.collect(1, seed=False)))
        # سيناريو «بملاحظات»: راوتر مفصول + راوتر ضعيف + جهاز بنج عالٍ.
        down_r = nas_repo.upsert_nas(NasDevice(id=None, name="ccr3", address="10.0.0.2",
                                               secret="s", vendor="mikrotik", enabled=True))
        db().execute("UPDATE nas_devices SET last_check_status='unreachable' WHERE id=?", (int(down_r.id),))
        weak_r = nas_repo.upsert_nas(NasDevice(id=None, name="rb-1", address="10.0.0.3",
                                               secret="s", vendor="mikrotik", enabled=True))
        db().execute("UPDATE nas_devices SET last_check_status='reachable' WHERE id=?", (int(weak_r.id),))
        rr.insert_sample(1, int(weak_r.id), sample={"ok": 1, "cpu_load": 91,
                         "temperature_c": 78.0, "mem_used_pct": 40.0, "disk_free_pct": 55.0})
        d_dn = dh.create_device(tenant_id=1, router_id=int(down_r.id), name="test",
                                interface_name="", ip_address="192.168.15.10",
                                network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
                                device_type="access_point")
        dh.set_status(tenant_id=1, device_id=d_dn, status="down")
        d_hl = dh.create_device(tenant_id=1, router_id=int(weak_r.id), name="cam-3",
                                interface_name="", ip_address="192.168.15.30",
                                network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
                                device_type="access_point")
        dh.set_status(tenant_id=1, device_id=d_hl, status="high_latency", latency_ms=210.0)
        db().commit()
        state = md.collect(1, seed=True)
        out.append("\n── تقرير دوريّ: بملاحظات ──")
        out.append(md.build_digest_message(state))
        out.append("\n── تذكير «ما زال مفصولًا» (لكل عنصر) ──")
        for item in state["down"]:
            out.append(md._reminder_message(item, "ساعتان و15 دقيقة"))
    return "\n".join(out)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل الشبكة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="periodic_shot_")
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
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    msgs = _sample_messages(app)
    with open(os.path.join(PREVIEW_DIR, "periodic_messages.txt"), "w", encoding="utf-8") as fh:
        fh.write(msgs)
    print("=== SAMPLE MESSAGES ===")
    print(msgs)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)
    url = f"http://127.0.0.1:{PORT}/admin/radius/alerts/resource-thresholds"

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
            pg.wait_for_timeout(800)
            pg.screenshot(path=os.path.join(PREVIEW_DIR, f"periodic_notifications_{label}.png"),
                          full_page=True)
            print(f"captured {label}")
            ctx.close()
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
