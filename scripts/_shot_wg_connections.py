"""Headless-chromium screenshots of the redesigned WireGuard connections page:
desktop (1440px) + mobile (390px). /static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_wgconn_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    html = (SRC / "wg_connections.html").as_uri()

    # desktop
    page = browser.new_page(viewport={"width": 1440, "height": 1000},
                            device_scale_factor=2)
    page.goto(html, wait_until="networkidle")
    page.wait_for_timeout(700)
    out = OUT_DIR / "wg_connections_desktop.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()

    # mobile
    page = browser.new_page(viewport={"width": 390, "height": 844},
                            device_scale_factor=2)
    page.goto(html, wait_until="networkidle")
    page.wait_for_timeout(700)
    out = OUT_DIR / "wg_connections_mobile.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()

    browser.close()
