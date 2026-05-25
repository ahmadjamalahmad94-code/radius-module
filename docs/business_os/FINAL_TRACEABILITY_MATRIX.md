# Business OS Final Traceability Matrix

This matrix maps Prompts 01-14 to implemented artifacts, tests, limitations, and follow-up work. It is scoped to `radius-module`.

| Prompt | Requirement | Implemented Files | Tests | Known Limitations | Follow-up |
|---|---|---|---|---|---|
| P01 | Master architecture, domain model, section map, execution rules | `docs/business_os/MASTER_ARCHITECTURE.md`, `DOMAIN_MODEL.md`, `SECTION_MAP.md`, `EXECUTION_RULES.md` | Compile-only; docs-only prompt | Contract only; no runtime implementation | Keep architecture updated as live integrations mature |
| P02 | Core wallet, ledger, events, pricing snapshots, revenue/profit foundations | `app/radius/db/migrations/056_business_os_core_foundations.sql`, `app/radius/services/business_os_finance.py` | `tests/test_business_os_core_foundations.py` | Foundations do not retrofit every legacy accounting flow | Expand adoption route-by-route |
| P03 | JSON API contracts for Business OS foundations | `app/api/v1/business_os.py`, API registration updates, service list/read methods | `tests/test_business_os_api_contracts.py` | API surface is foundation-grade, not every UI flow has API parity | Version and document external client contracts |
| P04 | Permission, scope, limit, and audit foundations | `app/radius/services/business_os_access.py` | `tests/test_business_os_access_foundations.py` | Foundation is not globally enforced on legacy routes | Adopt gates in future sensitive workflows |
| P05 | Finance Center web UI | `app/radius/services/business_os_finance_center.py`, `app/radius/routes/finance_center.py`, finance templates | `tests/test_finance_center_web.py` | Debts page is a transparent placeholder | Implement full debt lifecycle |
| P06 | Subscriber 360 lifecycle and renewal previews | `app/radius/services/subscriber_360.py`, subscriber routes/templates | `tests/test_subscriber_360.py` | Renewal preview does not live-apply RADIUS | Wire through existing payment workflows safely |
| P07 | Card users and card marketplace | `app/radius/db/migrations/057_card_users_marketplace.sql`, `app/radius/services/card_users_marketplace.py`, card-user routes/templates | `tests/test_card_users_marketplace.py` | Marketplace creates local cards, delivery is event-only | Add provider delivery under configured adapters |
| P08 | Card pricing, batch costing, manager wholesale accounting | `app/radius/db/migrations/058_card_pricing_costing.sql`, `app/radius/services/card_pricing.py`, pricing routes/templates | `tests/test_card_pricing_accounting.py` | Costing path does not replace existing card generation UI | Improve batch financial reporting/export |
| P09 | Manager/distributor operations, permissions, wallet limits | `app/radius/db/migrations/059_manager_distributor_policies.sql`, `app/radius/services/manager_distributor_ops.py`, routes/templates | `tests/test_manager_distributor_ops.py` | Distributor flows share foundations; tests focus on manager paths | Add distributor-specific action tests |
| P10 | Notifications/campaigns with queued-only providers | `app/radius/db/migrations/060_notification_campaign_engine.sql`, `app/radius/services/notification_campaigns.py`, communications routes/templates | `tests/test_notification_campaigns.py` | No real SMS/WhatsApp/Telegram/email provider enabled | Add providers only with secret configuration and retries |
| P11 | Events, audit, risk, investigation center | `app/radius/db/migrations/061_events_risk_center.sql`, `app/radius/services/events_risk_center.py`, events routes/templates | `tests/test_events_risk_center.py` | Risk scans create flags only; no automatic repair | Add review/assignment workflow |
| P12 | Operations Center and Speed Control dry-run | `app/radius/db/migrations/062_operations_speed_center.sql`, `app/radius/services/operations_speed_center.py`, operations routes/templates | `tests/test_operations_speed_center.py` | No live CoA or MikroTik speed change | Build guarded CoA apply path later |
| P13 | Dashboards, reports, archives, drill-down analytics | `app/radius/db/migrations/063_dashboard_report_archives.sql`, `app/radius/services/dashboard_reports.py`, report templates/routes | `tests/test_dashboard_reports_archives.py` | Archive stores calculated summaries, not binary exports | Add exports for executive reports |
| P14 | Subscriber and card-user portals | `app/radius/db/migrations/064_customer_portal_requests.sql`, `app/radius/services/customer_portals.py`, portal routes/templates | `tests/test_customer_portals.py` | Portal URLs are under `/admin/radius/portal/...`; payment gateway is placeholder | Add public blueprint/domain and payment provider integration |

## Cross-Cutting Safety Trace

- Financial ledger entries remain append-only; no financial delete routes were added.
- Report archive snapshots insert immutable calculated summaries and never overwrite existing snapshots.
- Notifications are queued-only unless future provider configuration is added.
- Operations speed policies persist as dry-run records with `applied_to_radius=false`.
- Portal routes are self-scoped and use separate portal session keys.
- No `radius-module-admin` or Flutter files were touched by Prompts 01-14.

## Test Map

Core targeted suite:

```powershell
python -m pytest tests/test_business_os_core_foundations.py tests/test_business_os_api_contracts.py tests/test_business_os_access_foundations.py tests/test_finance_center_web.py tests/test_subscriber_360.py tests/test_card_users_marketplace.py tests/test_card_pricing_accounting.py tests/test_manager_distributor_ops.py tests/test_notification_campaigns.py tests/test_events_risk_center.py tests/test_operations_speed_center.py tests/test_dashboard_reports_archives.py tests/test_customer_portals.py -q
```

## Release Follow-Up Queue

1. Add Flutter parity from the documented API contracts.
2. Add production provider integrations for notifications with configured secrets only.
3. Build guarded live CoA/speed apply with dry-run, audit, and rollback.
4. Move portal routes to a customer-facing non-admin URL/blueprint.
5. Add debt lifecycle workflows and debt-specific reporting.
6. Expand distributor-specific operational tests and UI.
7. Add full executive report CSV/XLSX/PDF exports.
