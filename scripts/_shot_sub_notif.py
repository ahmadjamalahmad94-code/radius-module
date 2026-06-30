"""Screenshot the subscriber-notifications editor showing per-template SMS
counters; types a long Arabic message into the first template to surface the
over-60 warning.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path("_sms_shots").resolve()
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
OUT_DIR.mkdir(parents=True, exist_ok=True)

html = SRC / "sub_notif.html"
LONG_AR = "اشتراكك «{username}» سينتهي قريبًا بتاريخ {exp} — يُرجى التجديد الآن لتجنّب انقطاع الخدمة عنك. شكرًا لك."

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--force-color-profile=srgb"])
    page = browser.new_page(viewport={"width": 1200, "height": 1000}, device_scale_factor=2)
    page.goto(html.as_uri(), wait_until="networkidle")
    # Fill the first template textarea long to show the warning; others keep
    # their short (≤60) defaults so a green single-segment counter shows too.
    page.eval_on_selector(
        ".sn-tmpl",
        "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); }",
        LONG_AR,
    )
    page.wait_for_timeout(500)
    out = OUT_DIR / "sub_notif_counter.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out}")
    page.close()
    browser.close()
