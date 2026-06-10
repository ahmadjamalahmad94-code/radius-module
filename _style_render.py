"""Render a broad set of admin pages on the style-unification branch to
verify visual consistency after the global CSS/JS bridge. Writes one
screenshot per page to _style_<slug>.png at 1440 x 1100.

Launches a fresh dev server from C:\\Projects\\wt-style-unify on port 5053
with a temp DB so the layout/CSS changes there are what render — no
interference with the main server on 5050/5051.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

WORKTREE = Path(r"C:\Projects\wt-style-unify")
PORT = 5053
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
DB = WORKTREE / "instance" / "_style_render.db"
OUT_DIR = Path(r"C:\Projects\radius-module")

# Page slug → relative URL. Each slug becomes _style_<slug>.png.
PAGES = [
    ("dashboard",          "/dashboard"),
    ("subscribers",        "/users"),
    ("plans_list",         "/plans"),
    ("plans_form",         "/plans/new"),
    ("cards_batches",      "/cards/batches"),
    ("cards_list",         "/cards"),
    ("cards_overview",     "/cards/overview"),
    ("cards_checker",      "/cards/checker/v2"),
    ("sessions",           "/sessions"),
    ("network_devices",    "/network-devices"),
    ("network_policy",     "/network-policies"),
    ("mt_list",            "/mt"),
    ("audit",              "/audit"),
    ("backups",            "/backups"),
    ("communications",     "/communications"),
    ("comm_channels",      "/communications/channels"),
    ("events_center",      "/events"),
    ("events_invest",      "/events/investigations"),
    ("events_risk",        "/events/risk"),
    ("events_security",    "/events/security"),
    ("finance_center",     "/finance"),
    ("finance_revenue",    "/finance/revenue"),
    ("finance_wallets",    "/finance/wallets"),
    ("admins_list",        "/admins"),
    ("admins_form",        "/admins/new"),
    ("roles_list",         "/roles"),
    ("settings",           "/settings"),
    ("recycle_bin",        "/recycle-bin"),
    ("tokens",             "/tokens"),
    ("bandwidth_list",     "/bandwidth"),
    ("hotspot_errors",     "/hotspot-errors"),
    ("store_support",      "/store-support"),
    ("distributors",       "/distributors"),
    ("invoices",           "/invoices"),
    ("tickets",            "/tickets"),
    ("business_operators", "/business-operators"),
    ("customer_portals",   "/customer-portals"),
    ("card_marketplace",   "/card-marketplace"),
    ("card_users",         "/card-users"),
    ("reports_archive",    "/reports/archive"),
    ("recharge_panel",     "/recharge"),
    ("share_groups",       "/share_groups"),
]


def _seed():
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        for ext in ("", "-shm", "-wal"):
            try: (DB.parent / (DB.name + ext)).unlink()
            except Exception: pass
    os.environ["HOBERADIUS_DB_PATH"] = str(DB)
    sys.path.insert(0, str(WORKTREE))
    from app import create_app  # noqa
    create_app()


def _wait_up(url, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500: return True
        except Exception: time.sleep(0.4)
    return False


def main() -> int:
    _seed()
    env = os.environ.copy()
    env["HOBERADIUS_DB_PATH"] = str(DB)
    env["FLASK_APP"] = "wsgi:app"
    env["PYTHONPATH"] = str(WORKTREE)
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "wsgi:app", "run",
         "--host", "127.0.0.1", "--port", str(PORT), "--no-reload"],
        cwd=str(WORKTREE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_up(BASE + "/admin/radius/login", 50):
            out, _ = proc.communicate(timeout=3)
            print("server failed to come up; tail:")
            print(out.decode("utf-8", errors="replace")[-3000:])
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1100},
                                      device_scale_factor=1, locale="ar")
            page = ctx.new_page()
            try:
                page.goto(ADMIN + "/login", wait_until="networkidle")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "admin")
                page.click('button[type="submit"], input[type="submit"]')
                page.wait_for_load_state("networkidle")

                missing = []
                for slug, path in PAGES:
                    out = OUT_DIR / f"_style_{slug}.png"
                    try:
                        page.goto(ADMIN + path, wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(700)
                        page.screenshot(path=str(out), full_page=False)
                        print(f"OK   {slug:24s} -> {out.name}")
                    except Exception as exc:
                        missing.append((slug, path, repr(exc)))
                        print(f"SKIP {slug:24s} ({path}) -> {exc!r}")
                if missing:
                    print(f"\n{len(missing)} pages did not render:")
                    for s, p_, e in missing: print(f"  - {s}: {p_} :: {e}")
            except Exception as exc:
                print(f"FAIL: {exc!r}")
                traceback.print_exc()
                return 1
            finally:
                ctx.close()
                browser.close()
    finally:
        try: proc.terminate(); proc.wait(timeout=5)
        except Exception: proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
