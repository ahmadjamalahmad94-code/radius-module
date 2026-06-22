# -*- coding: utf-8 -*-
"""لقطات PNG لميزة مقاييس موارد الراوتر:
  (1) بطاقة «موارد الراوتر» على لوحة راوتر فعليّ (RB5009) — قيم CPU/حرارة/
      ذاكرة/قرص/حركة، مع تظليل أحمر لمقياس متجاوز.
  (2) نفس البطاقة على راوتر سحابيّ (CHR) — الحرارة «غير متوفر» (لا حسّاس).
  (3) صفحة ضبط حدود التنبيهات.

التشغيل:  python tools/capture_resource_metrics.py
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
PORT = 5414


def _seed(app):
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import (admins_repo, tenants_repo, nas_repo,
                                          router_resource_repo as rr)
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل الشبكة", is_super_admin=True)
        hw = nas_repo.upsert_nas(NasDevice(
            id=None, name="راوتر المبنى الرئيسي", address="10.0.0.1", secret="s",
            vendor="mikrotik", enabled=True))
        chr_ = nas_repo.upsert_nas(NasDevice(
            id=None, name="راوتر سحابي CHR", address="10.0.0.2", secret="s",
            vendor="mikrotik", enabled=True))
        # راوتر فعليّ: المعالج 91% متجاوز (تظليل أحمر) + بقيّة القيم طبيعية.
        rr.insert_sample(1, int(hw.id), sample={
            "ok": 1, "cpu_load": 91, "mem_used_pct": 63.0, "mem_total_bytes": 1_000_000_000,
            "disk_free_pct": 38.0, "disk_total_bytes": 128_000_000, "temperature_c": 56.0,
            "voltage": 24.1, "traffic_in_bps": 47_000_000, "traffic_out_bps": 8_300_000,
            "rx_bytes_total": 9_000_000_000, "tx_bytes_total": 3_000_000_000,
            "uptime": "12d4h", "board_name": "RB5009UG+S+", "version": "7.15.3"})
        # راوتر CHR: لا حسّاس حرارة ⇒ «غير متوفر».
        rr.insert_sample(1, int(chr_.id), sample={
            "ok": 1, "cpu_load": 22, "mem_used_pct": 41.0, "mem_total_bytes": 512_000_000,
            "disk_free_pct": 72.0, "disk_total_bytes": 256_000_000, "temperature_c": None,
            "voltage": None, "traffic_in_bps": 2_100_000, "traffic_out_bps": 540_000,
            "rx_bytes_total": 1_000_000_000, "tx_bytes_total": 400_000_000,
            "uptime": "3d9h", "board_name": "CHR", "version": "7.15.3"})
        return int(hw.id), int(chr_.id)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل الشبكة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="res_metrics_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    hw_id, chr_id = _seed(app)
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
        for label, w in (("desktop", 1366), ("mobile", 412)):
            ctx = b.new_context(viewport={"width": w, "height": 1000},
                                device_scale_factor=2, locale="ar")
            ctx.add_cookies([{"name": "session", "value": cookie,
                              "domain": "127.0.0.1", "path": "/"}])
            pg = ctx.new_page()
            # بطاقة الموارد على الراوتر الفعليّ (تظليل أحمر للمعالج).
            pg.goto(f"{base}/mt/{hw_id}/dashboard", wait_until="load", timeout=20000)
            pg.wait_for_timeout(900)
            card = pg.query_selector("[data-mt-resource]")
            if card:
                card.scroll_into_view_if_needed()
                pg.wait_for_timeout(300)
                card.screenshot(path=os.path.join(PREVIEW_DIR, f"resource_card_hw_{label}.png"))
            # بطاقة الموارد على CHR (الحرارة غير متوفّرة).
            pg.goto(f"{base}/mt/{chr_id}/dashboard", wait_until="load", timeout=20000)
            pg.wait_for_timeout(700)
            card = pg.query_selector("[data-mt-resource]")
            if card:
                card.scroll_into_view_if_needed()
                pg.wait_for_timeout(300)
                card.screenshot(path=os.path.join(PREVIEW_DIR, f"resource_card_chr_{label}.png"))
            # صفحة ضبط الحدود.
            pg.goto(f"{base}/alerts/resource-thresholds", wait_until="load", timeout=20000)
            pg.wait_for_timeout(800)
            pg.screenshot(path=os.path.join(PREVIEW_DIR, f"resource_thresholds_{label}.png"),
                          full_page=True)
            print(f"captured {label}")
            ctx.close()
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
