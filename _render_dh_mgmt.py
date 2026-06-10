# -*- coding: utf-8 -*-
"""لقطات معاينة لصفحة تتبع حالة الأجهزة بعد الإدارة الاحترافية."""
import sys
import traceback

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            locale="ar", device_scale_factor=2,
        )
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")
            page.goto(ADMIN + "/device-health",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            page.screenshot(path="_dh_mgmt_full.png", full_page=True)
            for sel, name in (
                ("#dh-pollsettings", "_dh_mgmt_settings.png"),
                ("#dh-checks-stats", "_dh_mgmt_stats.png"),
                ("#dh-checks-wrap", "_dh_mgmt_history.png"),
            ):
                loc = page.locator(sel)
                if loc.count():
                    loc.first.screenshot(path=name)
                    print("OK", name)
                else:
                    print("MISSING", sel)
            # نافذة تفاصيل دورة من السجل
            btn = page.locator("[data-dh-check-details]").first
            if btn.count():
                btn.click()
                page.wait_for_timeout(400)
                page.locator("#dh-check-modal").screenshot(
                    path="_dh_mgmt_details.png")
                print("OK _dh_mgmt_details.png")
            print("DONE")
            return 0
        except Exception as exc:  # noqa: BLE001
            print("FAIL:", repr(exc))
            traceback.print_exc()
            try:
                page.screenshot(path="_dh_mgmt_full.png", full_page=True)
            except Exception:
                pass
            return 1
        finally:
            ctx.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
