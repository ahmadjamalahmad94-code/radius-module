# لقطات تحقّق لسحب «عربية أعمدة الإدارة/التقارير/الإعدادات/التدقيق».
# يسجّل الدخول admin/admin على خادم الـworktree (:5077) ويلتقط كل صفحة نطاق.
import sys, traceback
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5077/admin/radius"
OUT = r"C:\Projects\radius-module"

PAGES = [
    ("admins", "/admins"), ("roles", "/roles"), ("settings", "/settings"),
    ("audit", "/audit"), ("tokens", "/tokens"), ("reports_hub", "/reports"),
    ("login_states", "/reports/login_states"), ("login_status", "/reports/login_status"),
    ("failed_logins", "/reports/failed_logins"),
    ("manager_login_status", "/reports/manager_login_status"),
    ("sessions", "/reports/sessions"), ("mac_history", "/reports/mac_history"),
    ("coa_failures", "/reports/coa_failures"), ("speed_failures", "/reports/speed_failures"),
    ("manager_events", "/reports/manager_events"), ("user_events", "/reports/user_events"),
    ("profile_changes", "/reports/profile_changes"), ("api_messages", "/reports/api_messages"),
    ("used_cards", "/reports/used_cards"), ("cash_transactions", "/reports/cash_transactions"),
    ("balance_movements", "/reports/balance_movements"), ("archive", "/reports/archive"),
]


def main() -> int:
    ok, fail = 0, 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100},
                                  device_scale_factor=1, locale="ar")
        page = ctx.new_page()
        # تسجيل الدخول
        page.goto(BASE + "/login", wait_until="networkidle")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        for name, path in PAGES:
            try:
                page.goto(BASE + path, wait_until="domcontentloaded")
                page.wait_for_timeout(600)
                page.screenshot(path=f"{OUT}\\_sweep_admin_{name}.png", full_page=False)
                ok += 1
                print(f"OK  {name}")
            except Exception as exc:
                fail += 1
                print(f"FAIL {name}: {exc!r}")

        # برهان «بلا confirm متصفّح»: افتح مودال التأكيد على صفحة الرموز
        try:
            page.goto(BASE + "/tokens", wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            btn = page.query_selector("[data-confirm]")
            if btn:
                btn.click()
                page.wait_for_timeout(400)
                page.screenshot(path=f"{OUT}\\_sweep_admin_confirm_modal.png", full_page=False)
                print("OK  confirm_modal")
            else:
                print("no [data-confirm] on tokens")
        except Exception as exc:
            print(f"FAIL confirm_modal: {exc!r}")

        ctx.close(); browser.close()
    print(f"\nSHOTS ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
