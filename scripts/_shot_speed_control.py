"""Headless-chromium full-page screenshots of the redesigned speed-control page:
desktop (1440) + mobile (390). /static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_spdctl_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)
HTML = (SRC / "speed_control.html").as_uri()

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    # desktop
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto(HTML, wait_until="networkidle")
    page.wait_for_timeout(700)
    d = OUT_DIR / "speed_control_redesign_desktop.png"
    page.screenshot(path=str(d), full_page=True)
    print(f"wrote {d}")
    page.close()
    # mobile
    m = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3)
    m.goto(HTML, wait_until="networkidle")
    m.wait_for_timeout(700)
    mp = OUT_DIR / "speed_control_redesign_mobile.png"
    m.screenshot(path=str(mp), full_page=True)
    print(f"wrote {mp}")
    m.close()
    browser.close()
