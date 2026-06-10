"""Render the sections-admin UI + verify the gate works end-to-end:
  1. operator → 403 on each hidden-section URL.
  2. operator → 403 on /admin/radius/sections itself.
  3. super → reaches the page; screenshot it.
"""
from __future__ import annotations
import os, subprocess, sys, time, traceback, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

WORKTREE = Path(r"C:\Projects\wt-rbac-403")
PORT = 5054
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
DB = WORKTREE / "instance" / "_sections_admin.db"
OUT = Path(r"C:\Projects\radius-module") / "_render_sections_admin.png"


def _seed():
    DB.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("", "-shm", "-wal"):
        try: (DB.parent / (DB.name + ext)).unlink()
        except Exception: pass
    os.environ["HOBERADIUS_DB_PATH"] = str(DB)
    sys.path.insert(0, str(WORKTREE))
    from app import create_app  # noqa
    create_app()


def _wait_up(url, timeout=40):
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
    env["HOBERADIUS_DB_PATH"] = str(DB); env["FLASK_APP"] = "wsgi:app"
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
            print(out.decode("utf-8", errors="replace")[-2500:]); return 1

        # operator audit
        import http.client, urllib.parse
        c = http.client.HTTPConnection("127.0.0.1", PORT)

        def login(user, pw):
            body = urllib.parse.urlencode({"username": user, "password": pw})
            c.request("POST", "/admin/radius/login", body=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse(); cookie = r.getheader("Set-Cookie", ""); r.read()
            return cookie.split(";")[0] if cookie else ""

        def get(path, cookie):
            c.request("GET", path, headers={"Cookie": cookie})
            r = c.getresponse(); r.read(); return r.status

        op = login("operator", "operator")
        print("== OPERATOR direct-URL audit ==")
        targets = [
            ("/admin/radius/network/devices",        "network_ops_legacy"),
            ("/admin/radius/network/scan",           "network_ops_legacy"),
            ("/admin/radius/network/telegram",       "network_ops_legacy"),
            ("/admin/radius/mt-push-setup",          "dhcp_push"),
            ("/admin/radius/setup-wizard",           "engineering_setup"),
            ("/admin/radius/setup-wizard/fleet",     "fleet_setup"),
            ("/admin/radius/sections",               "sections_admin (super-only)"),
        ]
        for path, label in targets:
            status = get(path, op)
            mark = "OK 403 " if status == 403 else f"FAIL {status} "
            print(f"  {mark} {path:42s} ({label})")

        # super reaches all
        sup = login("admin", "admin")
        print("\n== SUPER direct-URL audit ==")
        for path, label in targets:
            status = get(path, sup)
            mark = "OK pass" if status != 403 else "FAIL 403"
            print(f"  {mark} ({status}) {path:42s} ({label})")

        # screenshot the sections admin UI
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1300},
                                      device_scale_factor=2, locale="ar")
            page = ctx.new_page()
            try:
                page.goto(ADMIN + "/login", wait_until="networkidle")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "admin")
                page.click('button[type="submit"], input[type="submit"]')
                page.wait_for_load_state("networkidle")
                page.goto(ADMIN + "/sections", wait_until="domcontentloaded")
                page.wait_for_selector('[data-testid="sections-admin-grid"]', timeout=10000)
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT), full_page=True)
                print(f"\nOK -> {OUT}")
            except Exception as e:
                print(f"FAIL: {e!r}"); traceback.print_exc(); return 1
            finally:
                ctx.close(); browser.close()
    finally:
        try: proc.terminate(); proc.wait(timeout=5)
        except Exception: proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
