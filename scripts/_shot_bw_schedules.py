"""Headless-chromium full-page screenshot of the rendered bandwidth-schedules
page (desktop 1440px). /static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_bwsched_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000},
                            device_scale_factor=2)
    html = SRC / "bandwidth_schedules.html"
    page.goto(html.as_uri(), wait_until="networkidle")
    page.wait_for_timeout(700)
    out = OUT_DIR / "bandwidth_schedules_edit_delete.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()
    browser.close()
