# تحقّق مركزي مستقل لقطاع التقارير/الأحداث/الإدارة/الإعدادات/الدعم.
# (1) يجمّع (parse) كل قوالب القطاع عبر بيئة Jinja للتطبيق — يكشف أخطاء البنية حتى لصفحات التفاصيل.
# (2) يرندر صفحات GET الفعلية عبر test_client (بدون تشغيل سيرفر) — يكشف أخطاء وقت الرندر.
import os, sys
os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")

from app import create_app
app = create_app()

# قوالب القطاع (بما فيها التفاصيل والأجزاء المشتركة) للفحص البنيوي
SECTOR_TEMPLATES = [
    "radius/rep_login_states.html","radius/rep_login_states_detail.html","radius/rep_login_status.html",
    "radius/rep_failed_logins.html","radius/rep_manager_login_status.html","radius/rep_sessions.html",
    "radius/rep_mac_history.html","radius/rep_coa_failures.html","radius/rep_speed_failures.html",
    "radius/rep_manager_events.html","radius/rep_user_events.html","radius/rep_profile_changes.html",
    "radius/rep_api_messages.html","radius/rep_used_cards.html","radius/rep_cash_transactions.html",
    "radius/rep_balance_movements.html","radius/reports_center.html","radius/reports_archive.html",
    "radius/reports_detail.html","radius/reports_nav.html","radius/events_center.html","radius/events_risk.html",
    "radius/events_security.html","radius/events_investigations.html","radius/events_detail.html",
    "radius/events_nav.html","radius/admins_list.html","radius/admins_form.html","radius/admins_profile_summary.html",
    "radius/admin_operations_nav.html","radius/admin_bridge.html","radius/admin_pricing.html","radius/roles_list.html",
    "radius/roles_form.html","radius/roles_permissions_nav.html","radius/audit_list.html","radius/audit_log_detail.html",
    "radius/audit_log_index.html","radius/licensing_index.html","radius/tenants_list.html","radius/tenants_form.html",
    "radius/settings_page.html","radius/backups.html","radius/recycle_bin.html","radius/sync_list.html",
    "radius/hotspot_errors.html","radius/data_protection_nav.html","radius/whatsapp.html","radius/tickets_list.html",
    "radius/tickets_form.html","radius/ticket_view.html","radius/lifecycle.html","radius/mt_alerts_index.html",
    "radius/mt_alerts_detail.html",
    "_partials/report_jump.html","_partials/repfilter.html",
]

# صفحات GET الفعلية (بدون معرّف) للرندر الكامل
GET_PAGES = [
    "/admin/radius/reports","/admin/radius/reports/financial","/admin/radius/reports/cards",
    "/admin/radius/reports/distributors","/admin/radius/reports/archive","/admin/radius/reports/login_states",
    "/admin/radius/reports/failed_logins","/admin/radius/reports/login_status","/admin/radius/reports/manager_login_status",
    "/admin/radius/reports/sessions","/admin/radius/reports/mac_history","/admin/radius/reports/coa_failures",
    "/admin/radius/reports/speed_failures","/admin/radius/reports/manager_events","/admin/radius/reports/user_events",
    "/admin/radius/reports/profile_changes","/admin/radius/reports/api_messages","/admin/radius/reports/used_cards",
    "/admin/radius/reports/cash_transactions","/admin/radius/reports/balance_movements",
    "/admin/radius/events","/admin/radius/events/risk","/admin/radius/events/security","/admin/radius/events/investigations",
    "/admin/radius/admins","/admin/radius/admins/new","/admin/radius/admins/pricing","/admin/radius/admins/profile-summary",
    "/admin/radius/roles","/admin/radius/roles/new","/admin/radius/audit","/admin/radius/licensing",
    "/admin/radius/tenants","/admin/radius/tenants/new","/admin/radius/settings","/admin/radius/backups",
    "/admin/radius/recycle-bin","/admin/radius/sync","/admin/radius/hotspot/errors","/admin/radius/whatsapp",
    "/admin/radius/tickets","/admin/radius/tickets/new","/admin/radius/lifecycle","/admin/radius/alerts",
]

parse_fail = []
with app.app_context():
    env = app.jinja_env
    for t in SECTOR_TEMPLATES:
        try:
            src = env.loader.get_source(env, t)[0]
            env.parse(src)
        except Exception as e:
            parse_fail.append((t, f"{type(e).__name__}: {e}"))

render_fail = []
with app.test_client() as c:
    with c.session_transaction() as s:
        s["admin_id"] = 1; s["admin_user"] = "audit"; s["admin_name"] = "Audit"
        s["is_super_admin"] = True; s["tenant_id"] = 1; s["_csrf_token"] = "verify-csrf"
    for u in GET_PAGES:
        try:
            r = c.get(u)
            if r.status_code >= 500:
                render_fail.append((u, f"HTTP {r.status_code}"))
        except Exception as e:
            render_fail.append((u, f"{type(e).__name__}: {e}"))

print("=== PARSE FAILURES ===")
for t, e in parse_fail: print(f"  PARSE  {t}\n         {e}")
print("=== RENDER (>=500) FAILURES ===")
for u, e in render_fail: print(f"  RENDER {u}\n         {e}")
print(f"\nSUMMARY: parse_ok={len(SECTOR_TEMPLATES)-len(parse_fail)}/{len(SECTOR_TEMPLATES)}  "
      f"render_ok={len(GET_PAGES)-len(render_fail)}/{len(GET_PAGES)}")
sys.exit(1 if (parse_fail or render_fail) else 0)
