"""Render the per-router dashboard at three viewport widths to verify the
new responsive tab strip. Run with the dev server already up on :5051.
Output PNGs land at C:\\Projects\\radius-module\\_render_router_tabs_<W>.png."""
from __future__ import annotations

import os
import sys
import traceback
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5051"
ADMIN = BASE + "/admin/radius"
NAS_ID = 1
SHOTS_DIR = r"C:\Projects\radius-module"

WIDTHS = [
    (1440, 900, "1440"),
    (768, 1100, "768"),
    (375, 800, "375"),
]


def main() -> int:
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--disable-features=BlockInsecurePrivateNetworkRequests",
        ])
        for width, height, label in WIDTHS:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2 if width <= 600 else 1,
                locale="ar",
                is_mobile=width <= 600,
                has_touch=width <= 900,
            )
            page = ctx.new_page()
            try:
                # admin login
                page.goto(ADMIN + "/login", wait_until="networkidle")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "admin")
                page.click('button[type="submit"], input[type="submit"]')
                page.wait_for_load_state("networkidle")

                # router dashboard — has an open EventSource so use domcontentloaded
                page.goto(ADMIN + f"/mt/{NAS_ID}/dashboard",
                          wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                # ensure the tab strip is present
                page.wait_for_selector(".mt-router-hero .mt-tabs", timeout=8000)
                page.wait_for_timeout(300)

                out = os.path.join(SHOTS_DIR, f"_render_router_tabs_{label}.png")
                # full_page so the entire tab bar + hero + first panel is captured.
                # For mobile we shoot the top viewport so the hero + tabs frame.
                page.screenshot(path=out, full_page=False)
                print(f"OK {label}px -> {out}")

                # Also click into the my-services tab once at 1440 so we can
                # confirm visually that the services are still top-level cards
                # after the tab redesign.
                if label == "1440":
                    page.click('[data-mt-tab="my-services"]')
                    page.wait_for_timeout(800)
                    page.screenshot(
                        path=os.path.join(SHOTS_DIR,
                                          "_render_router_tabs_my_services.png"),
                        full_page=False,
                    )
                    print("OK my-services tab -> captured")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {label}px: {exc!r}")
                traceback.print_exc()
                failures.append(label)
            finally:
                ctx.close()
        browser.close()
    if failures:
        print("FAILED widths:", failures)
        return 1
    print("ALL SHOTS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
