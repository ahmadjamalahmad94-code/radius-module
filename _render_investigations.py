"""Render /admin/radius/events/investigations after seeding a handful of
investigation rows that exercise each pill/severity/status path in the new
unified-design layout (status: open/in_review/closed; severity: info/
warning/error/critical). Output: _render_investigations.png

The dev server is launched fresh on port 5052 from the agent worktree
(C:\\Projects\\wt-investig) so the template changes there are the ones
rendered — independent of any other server on 5050/5051.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

WORKTREE = Path(r"C:\Projects\wt-investig")
PORT = 5052
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
DB = WORKTREE / "instance" / "_render_investigations.db"
OUT = r"C:\Projects\radius-module\_render_investigations.png"


def _seed(db_path: Path) -> None:
    """Bootstrap a minimal DB with the migrations applied + investigation
    rows that exercise the design system."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Run the app's migration entrypoint by importing the app — this applies
    # all migrations under the configured DB path (env var).
    os.environ["HOBERADIUS_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(WORKTREE))
    from app import create_app  # noqa: E402

    create_app()  # triggers migration run

    now = datetime.utcnow()
    rows = [
        ("محاولات دخول فاشلة متكررة على رقم 1024",
         "open", "critical", "subscriber", 1024, "admin",
         "خمس محاولات دخول فاشلة خلال 7 دقائق من عناوين IP مختلفة — يلزم التحقق إذا كانت محاولة سرقة جلسة.",
         (now - timedelta(minutes=12)).isoformat() + "Z"),
        ("خصم بنسبة 95٪ على فاتورة الموزّع #38",
         "in_review", "error", "distributor", 38, "ops_manager",
         "خصم استثنائي يتجاوز السقف اليومي — يلزم اعتماد الإدارة قبل الإقفال.",
         (now - timedelta(hours=2)).isoformat() + "Z"),
        ("محفظة برصيد سالب —‎ 312‎-",
         "open", "warning", "wallet", 7712, "auditor",
         "ظهرت بعد قيد عكسي لم يكتمل ربطه بدفعة. للمراجعة قبل تسوية الرصيد.",
         (now - timedelta(hours=5)).isoformat() + "Z"),
        ("سلف متكررة لنفس البطاقة خلال 24 ساعة",
         "open", "warning", "card", 8211, "admin",
         "ثلاث سلف نقدية متتابعة على بطاقة واحدة بدون سداد جزئي بينها.",
         (now - timedelta(hours=9)).isoformat() + "Z"),
        ("راوتر متوقف عن الرد منذ 30 دقيقة",
         "in_review", "info", "router", 8817, "noc",
         "الراوتر الأساسي لا يرد على الـAPI — جارٍ التحقق من الكهرباء والوصلة قبل تصعيد الحالة.",
         (now - timedelta(minutes=44)).isoformat() + "Z"),
        ("إيراد بلا قيد مطابق في دفتر الإيرادات",
         "closed", "info", "invoice", 5519, "finance",
         "وُجد القيد الناقص بعد المطابقة اليدوية وأُغلق الملف.",
         (now - timedelta(days=2)).isoformat() + "Z"),
        ("صلاحية الحذف منحت لمدير فرع بالخطأ",
         "closed", "warning", "manager", 21, "super_admin",
         "تمت إعادة ضبط الدور وإلغاء الصلاحية — ملف مغلق لأغراض المراجعة.",
         (now - timedelta(days=4)).isoformat() + "Z"),
    ]

    c = sqlite3.connect(str(db_path))
    try:
        c.execute("DELETE FROM investigations WHERE opened_by IN ('admin','ops_manager','auditor','noc','finance','super_admin')")
        for title, status, severity, ent_type, ent_id, opened_by, summary, created in rows:
            c.execute(
                "INSERT INTO investigations(tenant_id, title, status, severity, "
                "entity_type, entity_id, opened_by, summary, linked_events_json, "
                "linked_flags_json, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?)",
                (title, status, severity, ent_type, ent_id, opened_by, summary, created, created),
            )
        c.commit()
        print(f"OK seeded {len(rows)} investigations into {db_path}")
    finally:
        c.close()


def _wait_up(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main() -> int:
    if DB.exists():
        for ext in ("", "-shm", "-wal"):
            try:
                (DB.parent / (DB.name + ext)).unlink()
            except Exception:
                pass
    _seed(DB)

    env = os.environ.copy()
    env["HOBERADIUS_DB_PATH"] = str(DB)
    env["FLASK_APP"] = "wsgi:app"
    env["FLASK_ENV"] = "development"
    env["PYTHONPATH"] = str(WORKTREE)
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "wsgi:app", "run",
         "--host", "127.0.0.1", "--port", str(PORT), "--no-reload"],
        cwd=str(WORKTREE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_up(BASE + "/admin/radius/login", timeout=40):
            out, _ = proc.communicate(timeout=3)
            print("server failed to come up; tail of log:")
            print(out.decode("utf-8", errors="replace")[-3000:])
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1400},
                                      device_scale_factor=2, locale="ar")
            page = ctx.new_page()
            try:
                page.goto(ADMIN + "/login", wait_until="networkidle")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "admin")
                page.click('button[type="submit"], input[type="submit"]')
                page.wait_for_load_state("networkidle")

                page.goto(ADMIN + "/events/investigations",
                          wait_until="domcontentloaded")
                page.wait_for_selector('[data-testid="investigations-table"]',
                                       timeout=10000)
                page.wait_for_timeout(800)
                page.screenshot(path=OUT, full_page=True)
                print(f"OK -> {OUT}")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL: {exc!r}")
                traceback.print_exc()
                return 1
            finally:
                ctx.close()
                browser.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
