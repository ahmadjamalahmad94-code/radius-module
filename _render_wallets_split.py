"""Render /finance-center?tab=wallets and prove the wallets list is split into
three owner-type sections.

Seeds a few wallets of each owner type (subscriber, card_user, manager,
distributor, company), then logs in as admin/admin and screenshots the page.

Output: C:\\Projects\\radius-module\\_render_wallets_split.png
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback

DB = os.path.join(tempfile.mkdtemp(prefix="hr_render_walsplit_"), "render.db")
os.environ["HOBERADIUS_DB_PATH"] = DB
os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ.pop("HOBERADIUS_NO_SEED", None)
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

PORT = 5101
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
OUT = r"C:\Projects\radius-module\_render_wallets_split.png"


def _seed(app):
    from app.radius.services.business_os_finance import WalletService
    with app.app_context():
        ws = WalletService()
        # subscribers
        ws.create_wallet(tenant_id=1, owner_type="subscriber", owner_id=1, currency="ILS")
        ws.create_wallet(tenant_id=1, owner_type="subscriber", owner_id=2, currency="ILS")
        # card users
        ws.create_wallet(tenant_id=1, owner_type="card_user", owner_id=1, currency="ILS")
        ws.create_wallet(tenant_id=1, owner_type="card_user", owner_id=2, currency="ILS")
        ws.create_wallet(tenant_id=1, owner_type="card_user", owner_id=3, currency="ILS")
        # managers
        ws.create_wallet(tenant_id=1, owner_type="manager", owner_id=3, currency="ILS")
        ws.create_wallet(tenant_id=1, owner_type="manager", owner_id=4, currency="ILS")
        # distributor
        ws.create_wallet(tenant_id=1, owner_type="distributor", owner_id=5, currency="ILS")
        # company
        ws.create_wallet(tenant_id=1, owner_type="company", owner_id=None, currency="ILS")


def main() -> int:
    from app import create_app
    app = create_app()
    _seed(app)

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
        ctx = browser.new_context(viewport={"width": 1440, "height": 1700},
                                  locale="ar")
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            page.goto(BASE + "/admin/radius/finance-center?tab=wallets",
                      wait_until="networkidle")
            page.wait_for_selector('[data-fw-group="subscribers"]', timeout=8000)
            page.wait_for_selector('[data-fw-group="managers"]',    timeout=8000)
            page.wait_for_selector('[data-fw-group="distributors"]', timeout=8000)
            n_subs = page.locator('[data-fw-group="subscribers"] tbody tr').count()
            n_mgrs = page.locator('[data-fw-group="managers"] tbody tr').count()
            n_dist = page.locator('[data-fw-group="distributors"] tbody tr').count()
            print(f"subscribers/card-users rows: {n_subs}")
            print(f"managers/company rows: {n_mgrs}")
            print(f"distributors rows: {n_dist}")
            page.wait_for_timeout(500)
            page.screenshot(path=OUT, full_page=True)
            print(f"OK -> {OUT}")
            if n_subs < 1 or n_mgrs < 1 or n_dist < 1:
                rc = 2
        except Exception:
            traceback.print_exc()
            rc = 1
        finally:
            browser.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
