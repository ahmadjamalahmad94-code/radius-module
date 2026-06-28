"""Headless-chromium full-page screenshots of the rendered 403 page at desktop
(1280px) and mobile (390px) widths. Input HTML already has /static rewritten to
file:// so panel CSS/fonts resolve offline; the inline animated SVG renders too.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HTML = Path(sys.argv[1]).resolve()
OUT_DIR = Path(sys.argv[2])
OUT_DIR.mkdir(parents=True, exist_ok=True)
url = HTML.as_uri()

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    for label, width in (("desktop", 1280), ("mobile", 390)):
        page = browser.new_page(viewport={"width": width, "height": 900},
                                device_scale_factor=2)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(600)  # let fonts settle + animation reach idle frame
        out = OUT_DIR / f"forbidden_403_{label}_{width}.png"
        page.screenshot(path=str(out), full_page=True)
        print(f"wrote {out}")
        page.close()
    browser.close()
