"""Headless-chromium screenshots of the subscribers list with the per-row
«إدارية» menu opened to reveal «إرسال بيانات المشترك» (desktop 1440 + ~390px).
/static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_creds_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

html = SRC / "subscribers.html"


def _open_admin_menu(page):
    """Click the first row's «إدارية» trigger (2nd menu button in the row)."""
    try:
        triggers = page.query_selector_all("tbody tr [data-urow-trigger]")
        # tidy: pick the trigger labelled «إدارية» if present, else the 2nd.
        target = None
        for t in triggers:
            if "إدارية" in (t.inner_text() or ""):
                target = t
                break
        target = target or (triggers[1] if len(triggers) > 1 else (triggers[0] if triggers else None))
        if target:
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_timeout(400)
    except Exception as exc:  # noqa: BLE001
        print(f"menu-open skipped: {exc}")


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])

    # Desktop 1440 — open the admin menu then capture viewport (menu is a popover).
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto(html.as_uri(), wait_until="networkidle")
    page.wait_for_timeout(700)
    _open_admin_menu(page)
    out = OUT_DIR / "subscribers_creds_desktop.png"
    page.screenshot(path=str(out), full_page=False)
    print(f"wrote {out}")
    page.close()

    # Mobile ~390
    m = browser.new_page(viewport={"width": 390, "height": 900}, device_scale_factor=2)
    m.goto(html.as_uri(), wait_until="networkidle")
    m.wait_for_timeout(700)
    _open_admin_menu(m)
    out_m = OUT_DIR / "subscribers_creds_390.png"
    m.screenshot(path=str(out_m), full_page=False)
    print(f"wrote {out_m}")
    m.close()

    browser.close()
