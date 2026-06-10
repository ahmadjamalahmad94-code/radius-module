"""Render the «خدماتي» tab on the per-router dashboard while the live
inventory probe fails (simulated offline router), to verify that every
service card stays grey/«unknown» and never shows a fake «فعّال»
status — including the new paid «تغيير IP الخروج» card.

Dev server must be running on :5051. Output:
  C:\\Projects\\radius-module\\_render_services_status.png
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"
NAS_ID = 1
OUT = r"C:\Projects\radius-module\_render_services_status.png"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            locale="ar",
        )
        page = ctx.new_page()

        # Force the /inventory probe to fail (502) so the JS treats the
        # router as offline. This is the «router offline» path that used
        # to leak «فعّال» status pills.
        def _route(route):
            url = route.request.url
            if "/setup-wizard-v3/routers/" in url and url.endswith("/inventory"):
                route.fulfill(status=502, content_type="application/json",
                              body='{"ok":false,"error":"تعذّر الاتصال"}')
            else:
                route.continue_()

        page.route("**/*", _route)

        # Login
        page.goto(ADMIN + "/login", wait_until="networkidle")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Dashboard → my-services tab
        page.goto(ADMIN + f"/mt/{NAS_ID}/dashboard",
                  wait_until="domcontentloaded")
        page.wait_for_selector(".mt-router-hero .mt-tabs", timeout=8000)
        page.click('[data-mt-tab="my-services"]')
        # Let the (mocked) failed probe complete + DOM settle.
        page.wait_for_timeout(2500)

        # Scroll the «خدماتي» panel into view.
        page.evaluate(
            "document.querySelector('[data-mt-tab-panel=\"my-services\"]')"
            ".scrollIntoView({behavior:'instant',block:'start'})"
        )
        page.wait_for_timeout(400)

        page.screenshot(path=OUT, full_page=False)
        print(f"OK -> {OUT}")
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
