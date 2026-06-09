# تحقّق مركزي مستقل لقطاع المال والتحصيل والمحاسبة.
# يبني خريطة مسارات GET للقطاع تلقائياً ثم يرندرها عبر test_client (بلا سيرفر).
import os, sys, re
os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")

from app import create_app
app = create_app()

# قوالب القطاع للفحص البنيوي (Jinja parse)
SECTOR_TEMPLATES = [
    "radius/finance_center_hub.html", "radius/finance_accounting.html",
    "radius/finance_billing.html", "radius/finance_collection.html",
    "radius/finance_center.html", "radius/finance_debts.html",
    "radius/finance_loans.html", "radius/finance_revenue.html",
    "radius/finance_wallets.html", "radius/accounting_ledger.html",
    "radius/accounting_reports.html", "radius/admin_pricing.html",
    "radius/card_pricing.html", "radius/card_pricing_batch.html",
    "radius/cards_recharge_list.html", "radius/cards_recharge_batch.html",
    "radius/cards_recharge_new.html", "radius/recharge_panel.html",
    "radius/payments_lab.html", "radius/pay_demo.html",
    "radius/payment_collection_requests.html",
    "radius/payment_collection_request_detail.html",
    "radius/payment_collection_review_queue.html",
    "radius/payment_collection_reconciliation.html",
    "radius/payment_collection_settings.html",
    "radius/invoices_list.html", "radius/invoices_form.html",
    "radius/users_finance.html", "radius/_finance_nav.html",
]

# نمط مسارات القطاع — كل endpoint GET يطابقه يُرندر
SECTOR_RE = re.compile(
    r"finance|account|recharge|payments_lab|pay_demo|admin_pricing|"
    r"invoice|voucher|collection|payment_collection|card_pricing|cards_recharge"
)

parse_fail = []
with app.app_context():
    env = app.jinja_env
    for t in SECTOR_TEMPLATES:
        try:
            src = env.loader.get_source(env, t)[0]
            env.parse(src)
        except Exception as e:
            parse_fail.append((t, f"{type(e).__name__}: {e}"))

# جمع مسارات GET بلا وسائط (static rules) للقطاع
get_pages = []
for rule in app.url_map.iter_rules():
    ep = rule.endpoint
    if not ep.startswith("radius."):
        continue
    if "GET" not in (rule.methods or set()):
        continue
    if rule.arguments:  # نتجاوز ما يحتاج معرّفاً
        continue
    if not SECTOR_RE.search(ep):
        continue
    if ".json" in rule.rule or "export" in rule.rule:
        continue
    get_pages.append(rule.rule)
get_pages = sorted(set(get_pages))

render_fail = []
with app.test_client() as c:
    with c.session_transaction() as s:
        s["admin_id"] = 1; s["admin_user"] = "audit"; s["admin_name"] = "Audit"
        s["is_super_admin"] = True; s["tenant_id"] = 1; s["_csrf_token"] = "verify-csrf"
    for u in get_pages:
        try:
            r = c.get(u)
            if r.status_code >= 500:
                render_fail.append((u, f"HTTP {r.status_code}"))
        except Exception as e:
            render_fail.append((u, f"{type(e).__name__}: {e}"))

print("=== GET PAGES RENDERED ===")
for u in get_pages: print("  ", u)
print("=== PARSE FAILURES ===")
for t, e in parse_fail: print(f"  PARSE  {t}\n         {e}")
print("=== RENDER (>=500) FAILURES ===")
for u, e in render_fail: print(f"  RENDER {u}\n         {e}")
print(f"\nSUMMARY: parse_ok={len(SECTOR_TEMPLATES)-len(parse_fail)}/{len(SECTOR_TEMPLATES)}  "
      f"render_ok={len(get_pages)-len(render_fail)}/{len(get_pages)}")
sys.exit(1 if (parse_fail or render_fail) else 0)
