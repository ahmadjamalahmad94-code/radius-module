# New Work Sidebar Navigation Handoff

## Sidebar Files Found

- `app/templates/admin/_sidebar.html` is the shared sidebar partial for `/admin/radius` pages.
- `app/templates/admin/_admin_layout.html` includes the shared sidebar.
- The sidebar uses `sub_item(...)` and section-level `sec_*_active` flags for active/open state.

## Files Changed

- `app/templates/admin/_sidebar.html`
- `app/radius/routes/blueprint.py`
- `app/radius/routes/admin_bridge.py`
- `app/radius/routes/customer_portals.py`
- `app/templates/radius/admin_bridge.html`
- `app/templates/radius/customer_portals_admin.html`
- `tests/test_setup_wizard_sidebar_links.py`
- `tests/test_business_os_sidebar_navigation.py`
- `tests/test_admin_bridge_sidebar_navigation.py`
- `tests/test_customer_portals.py`
- `docs/setup_wizard/EXECUTION_LOG.md`
- `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
- `docs/handoffs/NEW_WORK_SIDEBAR_NAVIGATION_HANDOFF.md`

## Groups Added

- `الإعداد والتشغيل`
- `نظام الأعمال`
- `جسر الإدارة V40`

## Links Added

### الإعداد والتشغيل

- `/admin/radius/setup-wizard-v2` — `معالج الإعداد`
- `/admin/radius/setup-wizard/fleet` — `أسطول الراوترات`
- `/admin/radius/setup-wizard` — `عرض الإعداد الهندسي`

### نظام الأعمال

- `/admin/radius/dashboard` — `لوحة الأعمال`
- `/admin/radius/finance` — `المركز المالي`
- `/admin/radius/finance/wallets` — `المحافظ`
- `/admin/radius/finance/revenue` — `الإيرادات`
- `/admin/radius/finance/debts` — `الديون`
- `/admin/radius/finance/loans` — `القروض`
- `/admin/radius/finance/ledger` — `دفتر القيود`
- `/admin/radius/subscribers` — `المشتركين 360`
- `/admin/radius/card-users` — `مستخدمو البطاقات`
- `/admin/radius/card-marketplace` — `سوق البطاقات`
- `/admin/radius/card-pricing` — `تسعير البطاقات`
- `/admin/radius/business-operators` — `المدراء والموزعون`
- `/admin/radius/communications` — `التواصل والحملات`
- `/admin/radius/events` — `الأحداث والمخاطر`
- `/admin/radius/operations` — `مركز العمليات`
- `/admin/radius/operations/speed-control` — `التحكم بالسرعة`
- `/admin/radius/reports` — `التقارير`
- `/admin/radius/reports/financial` — `التقرير المالي`
- `/admin/radius/reports/cards` — `تقارير البطاقات`
- `/admin/radius/reports/distributors` — `تقارير الموزعين`
- `/admin/radius/reports/archive` — `الأرشيف`
- `/admin/radius/customer-portals` — `بوابات العملاء`

### جسر الإدارة V40

- `/admin/radius/admin-bridge` — `لوحة جسر الإدارة`

## Routes Verified As Existing

All sidebar links above are GET routes returning HTML. The V40 bridge and customer portal index routes were added as safe admin-only read/navigation pages.

## Missing / Not Exposed Routes

Not exposed by design:

- JSON/API-only routes such as `/api/v1/system/admin-bridge/capacity-status`
- Setup Wizard server WG readiness JSON route
- Setup Wizard run action routes
- Server peer apply/rollback routes
- Setup Wizard apply/rollback routes
- Added-services apply/action routes
- `/admin/radius/reports/archive/create`
- Customer portal POST action routes
- Dynamic detail routes requiring IDs
- Any POST, destructive, or automation route

## Tiny Admin-Only Helper Pages Added

- `/admin/radius/admin-bridge`: read-only V40 bridge navigation/status helper. It does not call remote admin automatically and does not execute POST actions.
- `/admin/radius/customer-portals`: read-only admin navigation helper for existing subscriber/card portal login pages.

## Active-State Behavior

- Setup Wizard V2, fleet, and engineering view use exact path matches so `/setup-wizard` does not activate `/setup-wizard/fleet`.
- Business OS links use exact path matches for concrete pages and scoped prefix matches only where the existing page family requires it.
- V40 bridge uses exact path matching for `/admin/radius/admin-bridge`.

## Tests Added / Updated

- `tests/test_setup_wizard_sidebar_links.py`
- `tests/test_business_os_sidebar_navigation.py`
- `tests/test_admin_bridge_sidebar_navigation.py`

## Exact Test Results

- `python -m compileall app` passed.
- `python -m pytest tests/test_setup_wizard_sidebar_links.py -q` passed: `3 passed, 807 warnings in 12.31s`.
- `python -m pytest tests/test_business_os_sidebar_navigation.py -q` passed: `3 passed, 808 warnings in 14.16s`.
- `python -m pytest tests/test_admin_bridge_sidebar_navigation.py -q` passed: `3 passed, 816 warnings in 12.65s`.
- `python -m pytest tests/test_finance_center_web.py tests/test_subscriber_360.py tests/test_card_users_marketplace.py tests/test_card_pricing_accounting.py tests/test_manager_distributor_ops.py tests/test_notification_campaigns.py tests/test_events_risk_center.py tests/test_operations_speed_center.py tests/test_dashboard_reports_archives.py tests/test_customer_portals.py -q` passed after replacing a brittle fixed marketplace-card username assertion with the actual purchased-card assertion: `55 passed, 14527 warnings in 46.67s`.
- `git diff --check` passed.

## Commit Hash

Recorded in the final handoff report after commit creation.

## Safety Confirmations

- No fake product functionality was created.
- No API/action routes were exposed in the sidebar.
- No live apply or production automation was enabled.
- No RADIUS auth/accounting behavior was changed.
- No MikroTik/FreeRADIUS/CoA behavior was changed.
- `radius-module-admin` was not touched.
- Flutter / `radius-module-app` was not touched.
- No unrelated dirty files were staged.
