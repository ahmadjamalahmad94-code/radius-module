"""رندر صفحة «سجلات الإيرادات» (تبويب الإيرادات في المركز المالي) إلى PNG.

ذاتيّ الاكتفاء: يشغّل Flask على DB مؤقتة مزروعة، يدرج بيانات إيراد حقيقية
بأنواع مصادر مختلفة (شراء بطاقة بمشترٍ+باقة، دفعة بطاقات بباقة، مبيعة
يدوية، ومفتاح مجهول لاختبار البديل العربي)، ثم يقود Playwright لتسجيل
الدخول والتقاط عمود «المصدر» للتأكد أنه عربيّ بأسماء حقيقية بلا إنجليزي.

الخرج: C:\\Projects\\radius-module\\_render_revenue_source.png
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import traceback

DB = os.path.join(tempfile.mkdtemp(prefix="hr_render_rev_"), "render.db")
os.environ["HOBERADIUS_DB_PATH"] = DB
os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ.pop("HOBERADIUS_NO_SEED", None)  # اترك بذرة admin/admin الافتراضية
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

PORT = 5097
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = BASE + "/admin/radius"
OUT_PAGE = r"C:\Projects\radius-module\_render_revenue_source.png"
NOW = "2026-06-09T10:00:00Z"


def _seed(app):
    from app.radius.db.connection import db

    with app.app_context():
        conn = db()
        plan_id = conn.execute(
            "INSERT INTO access_plans(tenant_id, name, created_at, updated_at) "
            "VALUES(1,?,?,?)",
            ("باقة الرندر", NOW, NOW),
        ).lastrowid
        # باقة سوق + مشترٍ + عملية شراء (مصدر card_user_purchase)
        pkg_id = conn.execute(
            "INSERT INTO card_marketplace_packages(tenant_id, name, plan_id, "
            "price_minor, currency, created_at) VALUES(1,?,?,?,?,?)",
            ("باقة 5 جيجا منزلية", plan_id, 1500, "ILS", NOW),
        ).lastrowid
        buyer_id = conn.execute(
            "INSERT INTO card_users(tenant_id, display_name, mobile, status, created_at) "
            "VALUES(1,?,?,?,?)",
            ("أحمد عبد الله التاجر", "0599123456", "active", NOW),
        ).lastrowid
        purchase_id = conn.execute(
            "INSERT INTO card_user_purchases(tenant_id, card_user_id, package_id, "
            "amount_minor, currency, status, delivery_status, created_at) "
            "VALUES(1,?,?,?,?,?,?,?)",
            (buyer_id, pkg_id, 1500, "ILS", "completed", "event_only", NOW),
        ).lastrowid
        # دفعة بطاقات (مصدر card_batch) باسم باقة حقيقي
        batch_id = conn.execute(
            "INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id, "
            "count, created_at) VALUES(1,?,?,?,?,?)",
            ("RC-2026-014", "باقة شهر كامل", plan_id, 50, NOW),
        ).lastrowid

        # سجلات الإيراد: نوعان حقيقيان + يدوي + مفتاح مجهول (اختبار البديل)
        revs = [
            ("card_user_purchase", purchase_id, 1500, 0, 1500),
            ("card_batch", batch_id, 5000, 3000, 2000),
            ("manual_sale", None, 800, 0, 800),
            ("mystery_future_key", 99, 400, 0, 400),
        ]
        for stype, sid, retail, wholesale, profit in revs:
            conn.execute(
                "INSERT INTO revenue_records(tenant_id, source_type, source_id, "
                "original_price_minor, retail_price_minor, wholesale_cost_minor, "
                "collected_amount_minor, net_profit_minor, company_share_minor, "
                "currency, status, metadata_json, created_at) "
                "VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (stype, sid, retail, retail, wholesale, retail, profit, profit,
                 "ILS", "posted", "{}", NOW),
            )
        conn.commit()
        print(f"seeded: purchase={purchase_id} batch={batch_id} buyer={buyer_id}")


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
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ar")
        page = ctx.new_page()
        try:
            page.goto(ADMIN + "/login", wait_until="networkidle")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin")
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")

            page.goto(ADMIN + "/finance-center?tab=revenue", wait_until="networkidle")
            page.wait_for_selector('th:has-text("المصدر")', timeout=8000)
            page.wait_for_timeout(400)

            # اقرأ خلية «المصدر» (العمود الثاني) لكل صف
            cells = page.eval_on_selector_all(
                'table:has(th:has-text("المصدر")) tbody tr td:nth-child(2)',
                "els => els.map(e => e.textContent.trim())",
            )
            print("=== خلايا عمود المصدر ===")
            for c in cells:
                print("   ", repr(c))

            import re
            english = [c for c in cells if re.search(r"[A-Za-z]", c)]
            has_id_hash = [c for c in cells if re.search(r"#\d", c)]
            print(f"خلايا فيها إنجليزي: {english}")
            print(f"خلايا فيها #id خام: {has_id_hash}")

            page.screenshot(path=OUT_PAGE, full_page=True)
            print(f"OK page -> {OUT_PAGE}")

            if not cells:
                print("FAIL: لا خلايا مصدر")
                rc = 2
            elif english or has_id_hash:
                print("FAIL: ما زال هناك إنجليزي أو #id خام في عمود المصدر")
                rc = 3
            else:
                print("PASS: عمود المصدر عربيّ بالكامل بأسماء حقيقية، صفر إنجليزي وصفر #id")
        except Exception:
            traceback.print_exc()
            rc = 1
        finally:
            browser.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
