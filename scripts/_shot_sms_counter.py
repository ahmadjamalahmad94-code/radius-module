"""Headless-chromium screenshots of the SMS connection page with the live
character/segment counter exercised — types a long Arabic message into the
test-send field so the counter + over-60 warning render.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_sms_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

html = SRC / "sms_connect.html"
# A >60-char Arabic message → Unicode, 2 segments → triggers the warning.
LONG_AR = "تنبيه مهم لجميع المشتركين: ستجري صيانة مجدولة للشبكة الليلة وقد تتأثر الخدمة مؤقتًا، نعتذر عن الإزعاج."

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])

    for label, width in (("desktop", 1440), ("390", 390)):
        page = browser.new_page(viewport={"width": width, "height": 1000}, device_scale_factor=2)
        page.goto(html.as_uri(), wait_until="networkidle")
        page.fill("#sms-message", LONG_AR)
        page.wait_for_timeout(500)
        out = OUT_DIR / f"sms_counter_{label}.png"
        page.screenshot(path=str(out), full_page=True)
        print(f"wrote {out}")
        page.close()

    browser.close()
