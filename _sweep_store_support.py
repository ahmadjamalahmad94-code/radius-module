"""Render /admin/radius/store-support من فرع sweep/store-arabic بعد بذر بيانات
تُظهر إصلاحات الكنسة: أسماء عربية حقيقية بدل «#معرّف»، fallback عربي محسوم،
نظام جداول uds للسجل القراءة-فقط، ونافذة تأكيد محلية بدل confirm() الأصلي.

يُشغّل سيرفر تطوير مؤقّت على منفذ 5071 من الـworktree.
المخرجات: C:\\Projects\\radius-module\\_sweep_store_*.png
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
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

WORKTREE = Path(r"C:\Projects\wt-sweep-store")
PORT = 5071
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
DB = WORKTREE / "instance" / "_sweep_store.db"
OUT_DIR = Path(r"C:\Projects\radius-module")

IDS: dict[str, int] = {}


def _seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    os.environ["HOBERADIUS_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(WORKTREE))
    from app import create_app  # noqa: E402
    create_app()  # migrations + seed_demo_data (admin/admin)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = OFF")
    now = datetime.utcnow().isoformat() + "Z"

    def cu(name: str, mobile: str) -> int:
        con.execute(
            "INSERT INTO card_users (tenant_id, display_name, mobile, created_at) "
            "VALUES (1, ?, ?, ?)", (name, mobile, now))
        return con.execute("SELECT last_insert_rowid() i").fetchone()["i"]

    # زبائن: اثنان باسم عربي، واحد بلا اسم (جوال فقط)، واحد بلا اسم وبلا جوال
    a = cu("أحمد عمر القنوع", "0599123456")
    b = cu("سمر خليل", "0567000111")
    c = cu("", "0561234567")     # يختبر fallback الجوال
    d = cu("", "")               # يختبر «زبون غير مُسمّى»
    IDS.update(a=a, b=b, c=c, d=d)

    def dep(cuid, claimed, method, status, payer, *, conf=None, ref="", note=""):
        con.execute(
            "INSERT INTO deposit_requests (tenant_id, card_user_id, method, payer_phone, "
            "reference, payer_name, amount_claimed_minor, status, confirmed_amount_minor, "
            "currency, admin_note, created_at, resolved_at, resolved_by) "
            "VALUES (1,?,?,?,?,?,?,?,?,'ILS',?,?,?,?)",
            (cuid, method, "0599123456", ref, payer, claimed, status,
             conf, note, now, (now if status != 'pending' else None),
             ("المدير" if status != 'pending' else "")))

    # طلبات شحن: معلّقة + محسومة (مؤكَّد/معدَّل/مرفوض)
    dep(a, 5000, "jawaly_pay", "pending", "أحمد عمر القنوع", ref="TXN-88812")
    dep(c, 2500, "bank", "pending", "")                      # payer فارغ ⇒ «غير معروف»
    dep(b, 10000, "jawaly_pay", "confirmed", "سمر خليل", conf=10000)
    dep(a, 8000, "palpay", "adjusted", "أحمد عمر القنوع", conf=7500)
    dep(b, 4000, "bank", "rejected", "سمر خليل", note="صورة الوصل غير واضحة")

    def wd(cuid, amount, payee, account, status, *, note=""):
        con.execute(
            "INSERT INTO withdrawal_requests (tenant_id, card_user_id, payee_name, "
            "payee_account, method, amount_minor, currency, status, admin_note, "
            "created_at, resolved_at, resolved_by) "
            "VALUES (1,?,?,?,'bank',?,'ILS',?,?,?,?,?)",
            (cuid, payee, account, amount, status, note, now,
             (now if status != 'pending' else None),
             ("المدير" if status != 'pending' else "")))

    wd(a, 3000, "أحمد عمر القنوع", "0599123456", "pending")
    wd(b, 6000, "سمر خليل", "PAL-12345-IBAN", "confirmed")
    wd(c, 2000, "", "", "rejected", note="بيانات الحساب ناقصة")   # payee فارغ ⇒ «غير معروف»

    def pm(method, label, acct, order, active=1):
        con.execute(
            "INSERT INTO store_payment_methods (tenant_id, method, label, account_name, "
            "account_number, instructions, active, sort_order, created_at, updated_at) "
            "VALUES (1,?,?,?,?,?,?,?,?,?)",
            (method, label, "متجر النور", acct, "حوّل ثم ارفع صورة الوصل.",
             active, order, now, now))

    pm("jawaly_pay", "محفظة جوالي باي", "0599123456", 1)
    pm("bank", "بنك فلسطين", "PAL-9988-7766", 2)
    pm("palpay", "PalPay", "palpay@store", 3)
    pm("other", "محفظة أخرى", "0561112223", 4, active=0)

    def msg(cuid, sender, body, actor=""):
        con.execute(
            "INSERT INTO store_chat_messages (tenant_id, card_user_id, sender, body, "
            "admin_actor, created_at) VALUES (1,?,?,?,?,?)",
            (cuid, sender, body, actor, now))

    msg(a, "customer", "متى يصلني الرصيد بعد التحويل؟")
    msg(a, "admin", "خلال دقائق بعد مراجعة الوصل، شكرًا لك.", actor="المدير")
    msg(c, "customer", "لم أستلم رمز الشحن.")        # خيط بلا اسم ⇒ جوال
    msg(d, "customer", "مرحبًا، عندي استفسار.")        # خيط بلا اسم وبلا جوال

    con.commit()
    con.close()


def _wait_ready(port: int, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for path in ["/ping", "/admin/radius/login", "/admin/radius"]:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1)
                return True
            except urllib.error.HTTPError:
                return True
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
        cwd=str(WORKTREE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_ready(PORT):
            print("Server did not start in time")
            return 1

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

            page.goto(f"{ADMIN}/store-support")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(900)

            # 1) تبويب الشحن (الافتراضي): معلّق + جدول uds للمحسوم
            page.screenshot(path=str(OUT_DIR / "_sweep_store_deposits.png"), full_page=True)
            print("[ok] _sweep_store_deposits.png")

            # 2) تبويب السحب
            page.click('[data-ssp-tab="withdrawals"]')
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT_DIR / "_sweep_store_withdrawals.png"), full_page=True)
            print("[ok] _sweep_store_withdrawals.png")

            # 3) تبويب الشات مع خيط مفتوح لاسم عربي حقيقي
            page.goto(f"{ADMIN}/store-support?chat={IDS['a']}#chat")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT_DIR / "_sweep_store_chat.png"), full_page=True)
            print("[ok] _sweep_store_chat.png")

            # 4) تبويب محافظ الاستلام
            page.click('[data-ssp-tab="wallets"]')
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT_DIR / "_sweep_store_wallets.png"), full_page=True)
            print("[ok] _sweep_store_wallets.png")

            # 5) نافذة التأكيد المحلية (بديل confirm الأصلي) — افتح أول نموذج حذف
            try:
                page.eval_on_selector(
                    'form[data-ssp-confirm]',
                    "f => f.querySelector('button[type=submit]').click()")
                page.wait_for_timeout(500)
                page.screenshot(path=str(OUT_DIR / "_sweep_store_confirm.png"))
                print("[ok] _sweep_store_confirm.png")
            except Exception as e:
                print("confirm shot skipped:", e)

        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
