"""Render /admin/radius/device-health to a PNG (headless, super-admin login).

Self-contained: starts the Flask app in-process on a fresh seeded temp DB,
inserts a router + a few sample devices with varied statuses, then drives
Playwright to log in as admin/admin and screenshot the page (table) and the
add-device modal.

Output:
  C:\\Projects\\radius-module\\_render_device_health.png         (page + table)
  C:\\Projects\\radius-module\\_render_device_health_modal.png   (add modal)
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import traceback

DB = os.path.join(tempfile.mkdtemp(prefix="hr_render_dh_"), "render.db")
os.environ["HOBERADIUS_DB_PATH"] = DB
os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ.pop("HOBERADIUS_NO_SEED", None)  # let default admin/admin seed
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

PORT = 5099
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
OUT_PAGE = r"C:\Projects\radius-module\_render_device_health.png"
OUT_MODAL = r"C:\Projects\radius-module\_render_device_health_modal.png"
OUT_IFACES = r"C:\Projects\radius-module\_render_device_health_interfaces.png"


def _seed(app):
    from datetime import datetime
    from app.radius.db.connection import transaction
    from app.radius.services import device_health as svc
    from app.radius.db.repos import device_health_repo as repo

    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT id FROM nas_devices WHERE tenant_id = 1 "
            "AND deleted_at IS NULL ORDER BY id LIMIT 1").fetchone()
        router_id = int(row["id"]) if row else 1
        samples = [
            ("نقطة وصول السطح", "ap", "ether2", "192.168.15.10", "السطح", "up", 7.0),
            ("وصلة LiteBeam الغربية", "litebeam", "ether3", "192.168.16.20", "البرج الغربي", "down", None),
            ("سويتش الطابق 2", "switch", "ether4", "192.168.17.5", "الطابق الثاني", "high_latency", 142.0),
            ("UniFi المدخل", "unifi", "ether5", "192.168.18.30", "المدخل الرئيسي", "up", 12.0),
            ("راوتر الفرع", "router", "ether6", "192.168.19.1", "فرع الشمال", "unknown", None),
        ]
        for name, typ, iface, ip, loc, status, lat in samples:
            out = svc.create_device(1, {
                "router_id": router_id, "name": name, "device_type": typ,
                "interface_name": iface, "ip_address": ip, "location": loc})
            repo.set_status(tenant_id=1, device_id=out["device_id"],
                            status=status, latency_ms=lat)


def _patch_interfaces(app):
    """The seeded router is offline; stub the admin client's interface read so
    the dependent dropdown demonstrably populates (WAN + tunnels included in the
    raw list so the screenshot proves they get excluded)."""
    from types import SimpleNamespace
    from app.radius.services import mikrotik_admin_client as mac
    rows = [
        {"name": "ether1", "type": "ether"},       # WAN uplink → excluded
        {"name": "ether2", "type": "ether"},       # LAN
        {"name": "ether3", "type": "ether"},       # LAN
        {"name": "ether4", "type": "ether"},       # LAN
        {"name": "bridge-lan", "type": "bridge"},  # LAN
        {"name": "pppoe-out1", "type": "pppoe-out"},   # tunnel → excluded
        {"name": "wg-mgmt", "type": "wireguard"},      # tunnel → excluded
        {"name": "lo", "type": "loopback"},            # loopback → excluded
    ]
    mac.interface_list = lambda nas: SimpleNamespace(ok=True, data=rows, error="")


def main() -> int:
    from app import create_app
    app = create_app()
    _seed(app)
    _patch_interfaces(app)

    server = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True)
    server.start()
    time.sleep(2.5)

    from playwright.sync_api import sync_playwright
    rc = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                                  locale="ar")
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            page.goto(ADMIN + "/device-health", wait_until="networkidle")
            page.wait_for_selector("#dh-table", timeout=8000)
            rows = page.locator("[data-dh-row]").count()
            print(f"table rows rendered: {rows}")
            page.wait_for_timeout(400)
            page.screenshot(path=OUT_PAGE, full_page=True)
            print(f"OK page -> {OUT_PAGE}")

            # Open the add-device modal and confirm it shows.
            page.click('[data-dh-add]')
            page.wait_for_selector("#dh-device-modal:not([hidden])", timeout=5000)
            # Expand the advanced section so its field hints are captured too.
            try:
                page.click("#dh-device-modal .dh-advanced summary")
            except Exception:
                pass
            page.wait_for_timeout(400)
            visible = page.locator("#dh-device-modal").is_visible()
            print(f"add modal visible: {visible}")
            page.screenshot(path=OUT_MODAL, full_page=False)
            print(f"OK modal -> {OUT_MODAL}")

            title = page.locator(".uds-hero-title, .hub-hero-title").first.inner_text()
            print(f"hero title: {title}")

            # ── Dependent interface dropdown: pick a router → interfaces load ──
            page.select_option('#dh-device-form [name="router_id"]', index=1)
            page.wait_for_selector("#dh-iface-select:not([hidden])", timeout=6000)
            page.wait_for_timeout(400)
            opts = page.eval_on_selector_all(
                "#dh-iface-select option", "els => els.map(e => e.value).filter(Boolean)")
            print(f"interface dropdown options: {opts}")
            wan_excluded = "ether1" not in opts and "pppoe-out1" not in opts and "wg-mgmt" not in opts
            print(f"WAN/tunnels excluded: {wan_excluded}")
            page.screenshot(path=OUT_IFACES, full_page=False)
            print(f"OK interfaces -> {OUT_IFACES}")

            if rows < 1 or not visible or not opts or not wan_excluded:
                rc = 2
        except Exception:
            traceback.print_exc()
            rc = 1
        finally:
            browser.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
