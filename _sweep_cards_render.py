"""Render the ELECTRONIC-CARDS sweep pages from wt-sweep-cards and screenshot
each to C:\\Projects\\radius-module\\_sweep_cards_*.png.

Seeds card_users + a rich card_user_360 timeline (a KNOWN event key, an
UNKNOWN event key, and a financial event) to prove the timeline column shows
clear Arabic — never a raw English machine key — plus a recharge batch.

Output:
  _sweep_cards_users.png            (card users list)
  _sweep_cards_user360.png          (card user 360 — timeline)
  _sweep_cards_recharge_list.png    (recharge batches list)
  _sweep_cards_recharge_batch.png   (recharge batch cards)
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

WORKTREE = Path(r"C:\Projects\wt-sweep-cards")
PORT = 5073
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
DB = WORKTREE / "instance" / "_sweep_cards.db"
OUT_DIR = Path(r"C:\Projects\radius-module")

SEEDED = {"uid": None, "batch_id": None}


def _seed() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    os.environ["HOBERADIUS_DB_PATH"] = str(DB)
    sys.path.insert(0, str(WORKTREE))

    from app import create_app  # noqa: E402
    app = create_app()  # migrations + demo seed

    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService  # noqa: E402
    from app.radius.services.cards import get_cards_service  # noqa: E402

    with app.app_context():
        svc = CardUsersMarketplaceService(tenant_id=1)
        u1 = svc.create_card_user(display_name="أحمد محمد علي", mobile="0599123456", password="pass1234")
        svc.create_card_user(display_name="سميرة خالد يوسف", mobile="0598777111", password="pass1234")
        svc.create_card_user(display_name="محمود عبد الله حسن", mobile="0597000222", password="pass1234")
        uid = int(u1["id"])
        SEEDED["uid"] = uid
        # شحن المحفظة (يولّد سجلّ مالي/حركة محفظة)
        try:
            svc.recharge_wallet(card_user_id=uid, amount=25.0, actor="admin")
        except Exception:
            traceback.print_exc()

        # دفعة شحن مسبق (recharge_only=1) بفئات متعددة
        try:
            rb = get_cards_service().generate_recharge_batch(
                actor="admin",
                package_name="حزمة شحن تجريبية",
                denominations=[{"value": 5, "count": 8}, {"value": 10, "count": 4}],
            )
            b = rb.get("batch") if isinstance(rb, dict) else rb
            SEEDED["batch_id"] = int(getattr(b, "id", None) or (b.get("id") if isinstance(b, dict) else 0))
        except Exception:
            traceback.print_exc()

    # أحداث خام تُثبت التعريب: مفتاح معروف + مفتاح مجهول (يجب أن يسقط على
    # الفئة العربية لا على الكود الإنجليزي) + حدث مالي معروف.
    now = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    con = sqlite3.connect(DB)
    rows = [
        ("card", "info", "card_user.created", "تم إنشاء حساب العميل"),
        ("card", "info", "card.future_unmapped_action", "إجراء مستقبلي غير مُعرَّف في الخريطة"),
        ("financial", "info", "wallet.credit", "شحن إداري للمحفظة"),
        ("card", "warning", "card_user.password_updated", "تحديث كلمة مرور البوابة"),
    ]
    for cat, sev, key, msg in rows:
        con.execute(
            """INSERT INTO business_events
               (tenant_id, category, severity, actor_type, actor_id,
                target_type, target_id, event_key, message, created_at)
               VALUES (1,?,?, 'admin', 1, 'card_user', ?, ?, ?, ?)""",
            (cat, sev, SEEDED["uid"], key, msg, now),
        )
    con.commit()
    con.close()
    print("seeded uid=%s batch_id=%s" % (SEEDED["uid"], SEEDED["batch_id"]))


def _wait_ready(timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for path in ["/admin/radius/login", "/admin/radius"]:
            try:
                urllib.request.urlopen(f"{BASE}{path}", timeout=1)
                return True
            except urllib.error.HTTPError:
                return True
            except Exception:
                pass
        time.sleep(0.4)
    return False


def main() -> int:
    try:
        _seed()
    except Exception:
        traceback.print_exc()
        return 1

    env = os.environ.copy()
    env["HOBERADIUS_DB_PATH"] = str(DB)
    env["FLASK_DEBUG"] = "0"
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "app", "run",
         "--port", str(PORT), "--no-debugger", "--no-reload"],
        cwd=str(WORKTREE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_ready():
            print("Server did not start in time")
            return 1
        uid = SEEDED["uid"]
        bid = SEEDED["batch_id"]
        shots = [
            (f"{ADMIN}/card-users", "_sweep_cards_users.png"),
            (f"{ADMIN}/card-users/{uid}", "_sweep_cards_user360.png"),
            (f"{ADMIN}/cards/recharge", "_sweep_cards_recharge_list.png"),
            (f"{ADMIN}/cards/recharge/{bid}", "_sweep_cards_recharge_batch.png"),
        ]
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ar")
            page = ctx.new_page()
            page.goto(f"{ADMIN}/login")
            page.wait_for_load_state("networkidle")
            page.fill("input[name=username]", "admin")
            page.fill("input[name=password]", "admin")
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
            for url, name in shots:
                page.goto(url)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(700)
                out = str(OUT_DIR / name)
                page.screenshot(path=out, full_page=True)
                print("shot -> " + out)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
