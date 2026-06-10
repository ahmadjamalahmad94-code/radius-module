"""Render /admin/radius/audit at 1440px after seeding a handful of audit
rows that exercise each of the 4 column fixes:
  1. Action  → exact-map + composer (not «عملية على راوتر»)
  2. Target  → resolved NAS name (not «هدف 8817»)
  3. Router  → NAS name (not «#8817»)
  4. Details → Arabic sentence (not raw JSON)
Output: _render_audit.png
"""
from __future__ import annotations

import json
import os
import sys
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"
OUT = r"C:\Projects\radius-module\_render_audit.png"


def _seed():
    """Idempotently seed nas_devices + 4 audit rows that exercise the
    rendering path the owner flagged."""
    repo = Path(__file__).resolve().parent
    db_path = os.environ.get("HOBERADIUS_DB_PATH") or str(
        repo / "instance" / "hoberadius.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        # nas_devices id=8817 with name "MT-Demo-Core"
        existing = c.execute(
            "SELECT id FROM nas_devices WHERE id=8817"
        ).fetchone()
        now = datetime.utcnow().isoformat() + "Z"
        if not existing:
            c.execute(
                "INSERT INTO nas_devices (id, tenant_id, name, address, "
                "secret, vendor, nas_type, enabled, created_at, "
                "connection_mode, api_user, api_password) "
                "VALUES (8817, 1, 'MT-Demo-Core', '10.0.0.8817', 's', 'mikrotik', "
                "'hotspot', 1, ?, 'direct', 'u', 'p')", (now,))
        # Wipe any prior demo rows so reruns produce a stable look.
        c.execute(
            "DELETE FROM audit_log WHERE tenant_id=1 AND actor='render-demo'")
        rows = [
            # 1) Exact-map known action + port-services payload (details column
            #    will read «المنافذ: ether2, ether3 · …»)
            dict(
                action="mt.port_services.loop_detect.apply",
                target_type="mikrotik_nas", target_id="8817",
                router_id=8817, severity="info", result_status="success",
                payload={"ports": ["ether2", "ether3", "ether4"],
                         "slug": "loop_detect", "ok": True},
            ),
            # 2) Backup action — exact-map, clean Arabic
            dict(
                action="mt.backup.create",
                target_type="mikrotik_nas", target_id="8817",
                router_id=8817, severity="info", result_status="success",
                payload={"filename": "hr-backup-2026-06-09.backup",
                         "size": "12 KB"},
            ),
            # 3) Identity change — was the kind of vague action that used to
            #    fall back to «عملية على راوتر»; now «تعديل اسم المايكروتيك»
            dict(
                action="mt.identity.set",
                target_type="mikrotik_nas", target_id="8817",
                router_id=8817, severity="warning", result_status="success",
                payload={"before": "MT-HQ", "after": "MT-Demo-Core"},
            ),
            # 4) Unknown action whose composer used to read «عملية على راوتر»
            #    — now «إجراء جلسة» / similar (and details column shows the
            #    Arabic sentence).
            dict(
                action="mt.connection.test",
                target_type="mikrotik_nas", target_id="8817",
                router_id=8817, severity="info", result_status="success",
                payload={"result": "ok", "duration": "412ms"},
            ),
        ]
        for r in rows:
            c.execute(
                "INSERT INTO audit_log (tenant_id, actor, action, "
                "target_type, target_id, payload_json, router_id, "
                "severity, result_status, created_at) "
                "VALUES (1, 'render-demo', ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["action"], r["target_type"], r["target_id"],
                 json.dumps(r["payload"], ensure_ascii=False),
                 r["router_id"], r["severity"], r["result_status"], now),
            )
        c.commit()
        print(f"OK seeded 4 rows into {db_path}")
    finally:
        c.close()


def main() -> int:
    _seed()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1200},
                                  device_scale_factor=2,
                                  locale="ar")
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            page.goto(ADMIN + "/audit?q=render-demo",
                      wait_until="domcontentloaded")
            page.wait_for_selector('[data-audit-log-rows]', timeout=10000)
            page.wait_for_timeout(800)
            page.screenshot(path=OUT, full_page=False)
            print(f"OK -> {OUT}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: {exc!r}")
            traceback.print_exc()
            return 1
        finally:
            ctx.close()
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
