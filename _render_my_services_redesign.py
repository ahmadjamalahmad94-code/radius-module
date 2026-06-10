# -*- coding: utf-8 -*-
"""لقطة معاينة لتبويب «خدماتي» بعد تحسينات الشكل (يونيو 2026).

يعترض طلب /inventory ويعيد جردًا وهميًا بنفس شكل بيانات صفحة المستخدم
(35 خدمة على 7 مجموعات + عنصر يدوي + تاريخ انتهاء) حتى تظهر قائمة
«الخدمات المضافة» كاملة بلا راوتر حقيقي. الخادم على :5051.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"
NAS_ID = 1
OUT = r"C:\Projects\radius-module\_render_my_services_redesign.png"
OUT_FULL = r"C:\Projects\radius-module\_render_my_services_redesign_full.png"


def fake_inventory() -> dict:
    def items(names, manual=None, expires=None):
        out = []
        for n in names:
            out.append({
                "label": n,
                "target": n,
                "source": "operator" if (manual and n in manual) else "hoberadius",
                **({"expires_at": expires} if (expires and n == names[0]) else {}),
            })
        return out

    ports7 = ["ether2", "ether4", "ether5", "ether6", "ether7", "ether8", "sfp1"]
    ports8 = ["ether2", "ether3", "ether4", "ether5", "ether6", "ether7", "ether8", "sfp1"]
    return {
        "ok": True,
        "groups": [
            {"service_type": "hotspot", "items": items(ports7, manual={"ether2"})},
            {"service_type": "broadband", "items": items(ports8)},
            {"service_type": "block-sites", "items": items(["hot.com"])},
            {"service_type": "open-sites", "items": items(["bnck.com"])},
            {"service_type": "public-ip",
             "items": items(["mark-connection", "mark-routing", "mark-routing2"],
                            expires="2026-07-01")},
            {"service_type": "bt_wifi_block", "items": items(ports7)},
            {"service_type": "loop_detect", "items": items(ports8)},
        ],
    }


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ar")
        page = ctx.new_page()
        try:
            page.route(
                "**/setup-wizard-v3/routers/*/inventory",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(fake_inventory()),
                ),
            )
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            page.goto(ADMIN + f"/mt/{NAS_ID}/dashboard#tab-my-services",
                      wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            page.click('[data-mt-tab="my-services"]')
            page.wait_for_timeout(1800)
            page.wait_for_selector(".rh-added-row", timeout=10000)
            page.screenshot(path=OUT, full_page=False)
            page.screenshot(path=OUT_FULL, full_page=True)
            print("OK ->", OUT)
            print("OK ->", OUT_FULL)
            return 0
        except Exception as exc:  # noqa: BLE001
            print("FAIL:", repr(exc))
            traceback.print_exc()
            try:
                page.screenshot(path=OUT, full_page=True)
                print("debug shot ->", OUT)
            except Exception:
                pass
            return 1
        finally:
            ctx.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
