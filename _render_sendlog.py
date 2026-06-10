"""Render /admin/radius/communications/deliveries after seeding delivery rows
that exercise the recipient column fix (subscriber/card_user/manager/
distributor with real Arabic names instead of «subscriber #id»).

Dev server launched fresh on port 5058 from wt-sendlog-recipient.
Output: C:\\Projects\\radius-module\\_render_sendlog.png
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

WORKTREE = Path(r"C:\Projects\wt-sendlog-recipient")
PORT = 5058
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
DB = WORKTREE / "instance" / "_render_sendlog.db"
OUT = r"C:\Projects\radius-module\_render_sendlog.png"


def _seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    os.environ["HOBERADIUS_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(WORKTREE))
    from app import create_app  # noqa: E402
    create_app()  # runs all migrations + seed_demo_data

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = OFF")

    now = datetime.utcnow().isoformat() + "Z"

    # Pick real IDs from the already-seeded demo data (tenant 1)
    sub_rows = con.execute(
        "SELECT id, full_name, username, mobile FROM subscribers WHERE tenant_id=1 ORDER BY id LIMIT 4"
    ).fetchall()
    cu_row = con.execute(
        "SELECT id, display_name, mobile FROM card_users WHERE tenant_id=1 LIMIT 1"
    ).fetchone()
    dist_row = con.execute(
        "SELECT id, name, display_name FROM distributors WHERE tenant_id=1 LIMIT 1"
    ).fetchone()
    admin_row = con.execute(
        "SELECT id, full_name, username FROM admins ORDER BY id LIMIT 1"
    ).fetchone()

    # Drop existing test rows in case of re-run
    con.execute("DELETE FROM message_deliveries WHERE tenant_id=1")
    con.execute("DELETE FROM message_notifications WHERE tenant_id=1")

    # Ensure a template exists
    existing_tpl = con.execute(
        "SELECT id FROM notification_templates WHERE tenant_id=1 LIMIT 1"
    ).fetchone()
    tpl_id = existing_tpl["id"] if existing_tpl else None
    if tpl_id is None:
        con.execute("""
            INSERT INTO notification_templates(tenant_id, template_key, title, channel, subject, body, variables_json, created_at)
            VALUES (1,'renewal','تجديد','sms','','نص تجريبي','[]',?)
        """, (now,))
        tpl_id = con.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    # Build notification + delivery rows from real seeded data
    entries = []
    channels = ["sms", "whatsapp", "sms", "email"]
    statuses = ["delivered", "sent", "failed", "queued"]
    providers = ["sms_gw", "wa_gw", "sms_gw", "smtp"]
    for i, sub in enumerate(sub_rows):
        entries.append({
            "rtype": "subscriber",
            "rid": int(sub["id"]),
            "channel": channels[i % len(channels)],
            "status": statuses[i % len(statuses)],
            "provider": providers[i % len(providers)],
            "addr": sub["mobile"] or "",
        })
    if cu_row:
        entries.append({
            "rtype": "card_user",
            "rid": int(cu_row["id"]),
            "channel": "internal",
            "status": "sent",
            "provider": "internal",
            "addr": cu_row["mobile"] or "",
        })
    if admin_row:
        entries.append({
            "rtype": "manager",
            "rid": int(admin_row["id"]),
            "channel": "email",
            "status": "sent",
            "provider": "smtp",
            "addr": "admin@demo.com",
        })
    if dist_row:
        entries.append({
            "rtype": "distributor",
            "rid": int(dist_row["id"]),
            "channel": "sms",
            "status": "queued",
            "provider": "sms_gw",
            "addr": "",
        })
    entries.append({
        "rtype": "company",
        "rid": 1,
        "channel": "internal",
        "status": "delivered",
        "provider": "internal",
        "addr": "",
    })

    for entry in entries:
        con.execute("""
            INSERT INTO message_notifications
                (tenant_id, notification_type, channel, recipient_type, recipient_id,
                 template_id, subject, body, status, metadata_json, created_by, created_at)
            VALUES (1,'manual',?,?,?,?,'إشعار','نص تجريبي',?,'{}','admin',?)
        """, (entry["channel"], entry["rtype"], entry["rid"], tpl_id, entry["status"], now))
        nid = con.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        con.execute("""
            INSERT INTO message_deliveries
                (tenant_id, notification_id, channel, provider_key, recipient_address, status, created_at)
            VALUES (1,?,?,?,?,?,?)
        """, (nid, entry["channel"], entry["provider"], entry["addr"], entry["status"], now))

    con.commit()
    con.close()


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for path in ["/ping", "/admin/radius/login", "/admin/radius"]:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1)
                return True
            except urllib.error.HTTPError:
                return True  # got a real HTTP response
            except Exception:
                pass
        time.sleep(0.4)
    return False


def main() -> int:
    try:
        _seed(DB)
    except Exception:
        traceback.print_exc()
        return 1

    env = os.environ.copy()
    env["HOBERADIUS_DB_PATH"] = str(DB)
    env["FLASK_ENV"] = "development"
    env["FLASK_DEBUG"] = "0"
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "app", "run",
         "--port", str(PORT), "--no-debugger", "--no-reload"],
        cwd=str(WORKTREE),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not _wait_ready(PORT):
            print("Server did not start in time")
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="ar",
            )
            page = ctx.new_page()

            # Log in
            page.goto(f"{ADMIN}/login")
            page.wait_for_load_state("networkidle")
            page.fill("input[name=username]", "admin")
            page.fill("input[name=password]", "admin")
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")

            # Navigate to communications deliveries
            page.goto(f"{ADMIN}/communications/deliveries")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)

            page.screenshot(path=OUT, full_page=True)
            print(f"Screenshot saved → {OUT}")

        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
