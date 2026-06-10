# -*- coding: utf-8 -*-
"""لقطات معاينة لصفحة إدارة كشف اللوب بعد البناء (يونيو 2026)."""
import sys
import traceback

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"
NAS_ID = 1


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            locale="ar", device_scale_factor=2,
        )
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")
            page.goto(
                ADMIN + f"/mt/{NAS_ID}/port-services?slug=loop_detect",
                wait_until="domcontentloaded", timeout=60000,
            )
            page.wait_for_timeout(1200)
            page.screenshot(path="_loop_mgmt_full.png", full_page=True)
            for sel, name in (
                ("[data-pss-loop-table]", "_loop_mgmt_table.png"),
                ("[data-pss-loop-settings-form]", "_loop_mgmt_settings.png"),
                ("[data-pss-loop-history]", "_loop_mgmt_history.png"),
                ("[data-pss-loop-mismatch]", "_loop_mgmt_mismatch.png"),
            ):
                loc = page.locator(sel)
                if loc.count():
                    loc.first.screenshot(path=name)
                    print("OK", name)
                else:
                    print("MISSING", sel)
            print("DONE")
            return 0
        except Exception as exc:  # noqa: BLE001
            print("FAIL:", repr(exc))
            traceback.print_exc()
            try:
                page.screenshot(path="_loop_mgmt_full.png", full_page=True)
            except Exception:
                pass
            return 1
        finally:
            ctx.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
