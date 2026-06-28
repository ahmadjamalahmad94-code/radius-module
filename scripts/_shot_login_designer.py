"""Headless-chromium screenshot of the login-designer with the «المحتوى»
(Content) tab activated, proving the 3 under-image chip editors are present.
Desktop (1440px) + mobile (390px). /static already rewritten to file://.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_designer_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _activate_content_tab(page):
    # click the «المحتوى» tab so the chip editors (data-mtld-sec="content")
    # become visible, then give the layout a beat to settle.
    page.click('[data-mtld-tab="content"]')
    page.wait_for_timeout(500)


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    html = (SRC / "login_designer.html").as_uri()

    # desktop
    page = browser.new_page(viewport={"width": 1440, "height": 1000},
                            device_scale_factor=2)
    page.goto(html, wait_until="networkidle")
    page.wait_for_timeout(600)
    _activate_content_tab(page)
    out = OUT_DIR / "login_designer_content_desktop.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()

    # mobile
    page = browser.new_page(viewport={"width": 390, "height": 844},
                            device_scale_factor=2)
    page.goto(html, wait_until="networkidle")
    page.wait_for_timeout(600)
    _activate_content_tab(page)
    out = OUT_DIR / "login_designer_content_mobile.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()

    browser.close()
