"""Render the backups / restore-from-device section to verify the styled
file-upload control (no native «Choose File» button visible).
Output: _render_restore_upload.png
"""
from __future__ import annotations

import sys
import traceback
from playwright.sync_api import sync_playwright

BASE  = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"
OUT   = r"C:\Projects\radius-module\_render_restore_upload.png"


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

            page.goto(ADMIN + "/backups", wait_until="domcontentloaded")
            page.wait_for_timeout(800)

            # Scroll to the "رفع نسخة من جهازك" card (upload from device)
            upload_card = page.locator("text=رفع نسخة من جهازك").first
            upload_card.scroll_into_view_if_needed()
            page.wait_for_timeout(300)

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
