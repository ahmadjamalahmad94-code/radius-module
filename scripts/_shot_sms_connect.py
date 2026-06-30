"""Headless-chromium full-page screenshots of the rendered SMS connection page
(desktop 1440px + mobile ~390px). /static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_sms_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

html = SRC / "sms_connect.html"

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])

    # Desktop 1440
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto(html.as_uri(), wait_until="networkidle")
    page.wait_for_timeout(700)
    out = OUT_DIR / "sms_connect_desktop.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()

    # Mobile ~390 (iPhone-ish). Headless Chrome enforces a min viewport on some
    # DPRs, so we screenshot at width 390 directly with dpr=2.
    m = browser.new_page(viewport={"width": 390, "height": 850}, device_scale_factor=2)
    m.goto(html.as_uri(), wait_until="networkidle")
    m.wait_for_timeout(700)
    out_m = OUT_DIR / "sms_connect_390.png"
    m.screenshot(path=str(out_m), full_page=True)
    print(f"wrote {out_m}")
    m.close()

    browser.close()
