"""Headless-chromium full-page screenshots of the access-control page:
desktop (1440) + mobile (390). /static already rewritten to file://.

argv[1] = source html basename (e.g. access_control_before.html)
argv[2] = output label (e.g. before / after)
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_ac_spacing_shots").resolve()
HTML_NAME = sys.argv[1] if len(sys.argv) > 1 else "access_control.html"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "after"
HTML = (SRC / HTML_NAME).as_uri()

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto(HTML, wait_until="networkidle")
    page.wait_for_timeout(700)
    d = SRC / f"access_control_{LABEL}_desktop.png"
    page.screenshot(path=str(d), full_page=True)
    print(f"wrote {d}")
    page.close()
    m = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3)
    m.goto(HTML, wait_until="networkidle")
    m.wait_for_timeout(700)
    mp = SRC / f"access_control_{LABEL}_mobile.png"
    m.screenshot(path=str(mp), full_page=True)
    print(f"wrote {mp}")
    m.close()
    browser.close()
