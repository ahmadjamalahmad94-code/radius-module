"""Render /admin/radius/communications/templates and screenshot the form
to verify field alignment fix. Output: _render_template_form.png
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE  = "http://127.0.0.1:5052"
ADMIN = BASE + "/admin/radius"
OUT   = str(Path(__file__).resolve().parent / "_render_template_form.png")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            locale="ar",
        )
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            page.goto(ADMIN + "/communications/templates",
                      wait_until="domcontentloaded")
            page.wait_for_selector('.ctpl-form', timeout=10000)
            page.wait_for_timeout(600)

            # Scroll to the form section so it's fully visible
            page.evaluate("document.querySelector('.ctpl-form').scrollIntoView({block:'center'})")
            page.wait_for_timeout(300)

            page.screenshot(path=OUT, full_page=True)
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
