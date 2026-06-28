"""Headless-chromium full-page screenshots of the rendered recycle-bin and
lifecycle pages (desktop 1280px). /static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_rclc_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    for name in ("recycle_bin", "lifecycle"):
        html = SRC / f"{name}.html"
        page = browser.new_page(viewport={"width": 1280, "height": 900},
                                device_scale_factor=2)
        page.goto(html.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(700)
        out = OUT_DIR / f"{name}_desktop.png"
        page.screenshot(path=str(out), full_page=True)
        print(f"wrote {out}")
        page.close()
    browser.close()
