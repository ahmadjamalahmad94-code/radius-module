# -*- coding: utf-8 -*-
"""لقطة بعد توحيد موارد الراوتر (fix/router-resource-dedup-consistency):
سيناريو المالك (ccr3 / CCR1009-8G-1S، RouterOS 7.20.6) — القرص «مستخدم 38.1%»
وحرارة المعالج 49° في مكانٍ واحد فقط («موارد الراوتر»)؛ الشريط العلوي لم يَعُد
يُكرّر بطاقات الموارد (يَبقى التشغيل/الساعة/الإصدار).

التشغيل:  python tools/capture_resource_dedup.py
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
PORT = 5417


def _seed(app):
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import (admins_repo, tenants_repo, nas_repo,
                                          router_resource_repo as rr)
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل الشبكة", is_super_admin=True)
        nas = nas_repo.upsert_nas(NasDevice(
            id=None, name="ccr3", address="10.0.0.1", secret="s",
            vendor="mikrotik", enabled=True))
        # قيم لقطة المالك: المعالج 77% · الذاكرة 36.8% · القرص حرّ 61.9% (=مستخدم
        # 38.1%) · حرارة المعالج 49° (cpu-temperature، لا 35° اللوحة).
        rr.insert_sample(1, int(nas.id), sample={
            "ok": 1, "cpu_load": 77, "mem_used_pct": 36.8, "mem_total_bytes": 2_000_000_000,
            "disk_free_pct": 61.9, "disk_total_bytes": 128_000_000, "temperature_c": 49.0,
            "voltage": 24.1, "traffic_in_bps": 12_000_000, "traffic_out_bps": 3_400_000,
            "rx_bytes_total": 9_000_000_000, "tx_bytes_total": 3_000_000_000,
            "uptime": "12d4h", "board_name": "CCR1009-8G-1S", "version": "7.20.6"})
        return int(nas.id)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل الشبكة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="res_dedup_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    rid = _seed(app)
    cookie = _cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    url = f"http://127.0.0.1:{PORT}/admin/radius/mt/{rid}/dashboard"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        for label, w in (("desktop", 1366), ("mobile", 412)):
            ctx = b.new_context(viewport={"width": w, "height": 1400},
                                device_scale_factor=2, locale="ar")
            ctx.add_cookies([{"name": "session", "value": cookie,
                              "domain": "127.0.0.1", "path": "/"}])
            pg = ctx.new_page()
            pg.goto(url, wait_until="load", timeout=20000)
            pg.wait_for_timeout(1000)
            # نظرة عامة كاملة: الشريط العلوي (هويّة، بلا موارد) + قسم الموارد الوحيد.
            ov = pg.query_selector('[data-mt-tab-panel="overview"]') or pg
            ov.screenshot(path=os.path.join(PREVIEW_DIR, f"resource_dedup_overview_{label}.png"))
            card = pg.query_selector("[data-mt-resource]")
            if card:
                card.scroll_into_view_if_needed()
                pg.wait_for_timeout(200)
                card.screenshot(path=os.path.join(PREVIEW_DIR, f"resource_dedup_card_{label}.png"))
            print(f"captured {label}")
            ctx.close()
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
