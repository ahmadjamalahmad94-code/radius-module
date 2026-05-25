# Business OS Release Readiness

## Executive Summary

The Business OS foundation is ready for controlled internal validation inside `radius-module`. The implementation is additive and keeps existing RADIUS authentication/accounting behavior intact. It introduces finance, wallet, event, reporting, operational, notification, and portal foundations while keeping live network mutations either unchanged, dry-run only, queued-only, or explicitly out of scope.

This is not yet a full production rollout for all live ISP operations. Provider integrations, Flutter parity, public portal routing, and guarded live speed/CoA apply remain follow-up work.

## What Exists

- Master Business OS architecture and execution rules.
- Wallets, wallet transactions, immutable ledger entries, pricing snapshots, business events, revenue records, profit shares, archive snapshots, and approval foundations.
- API contracts for wallets, ledger, events, revenue, pricing snapshots, and summary.
- Permission/scope/limit/audit foundations.
- Web Finance Center.
- Subscriber 360 view and safe renewal previews.
- Card users, wallets, marketplace packages, purchases, and local card generation.
- Card pricing and batch financial costing.
- Manager/distributor operational policy foundations.
- Notification/campaign queue foundation.
- Events, risk, and investigations center.
- Read-only Operations Center and dry-run Speed Control Center.
- Executive reports, drill-down analytics, and immutable archive snapshots.
- Subscriber and card-user portal foundations.

## Dry-Run Only

- Renewal preview from Subscriber 360.
- Speed Control Center policies.
- Emergency operations placeholders.
- Notification action-coupled campaigns.
- Portal renewal requests.

## Queued/Event Only

- Notification delivery provider is queued-only by default.
- Card purchase delivery is event-only.

## What Touches Live RADIUS

No new live RADIUS mutation was introduced by Prompts 01-14.

Existing legacy RADIUS/auth/accounting paths remain in place and were not rewritten. Some existing accounting services can call existing activation helpers when explicitly requested by older flows; the new Business OS prompt work does not enable a new live path by default.

## What Does Not Touch Live Systems

- No MikroTik live write path was added.
- No new CoA/speed live push was added.
- No notification provider sends externally by default.
- No portal action mutates MikroTik or router configuration.
- No Flutter files were changed.
- No `radius-module-admin` files were changed.

## Deployment Notes

- Apply migrations in order through the existing migration runner.
- New migrations are additive:
  - `056_business_os_core_foundations.sql`
  - `057_card_users_marketplace.sql`
  - `058_card_pricing_costing.sql`
  - `059_manager_distributor_policies.sql`
  - `060_notification_campaign_engine.sql`
  - `061_events_risk_center.sql`
  - `062_operations_speed_center.sql`
  - `063_dashboard_report_archives.sql`
  - `064_customer_portal_requests.sql`
- Validate with the targeted Business OS suite before deployment.
- Keep provider secrets out of source and set them only through future configured provider adapters.

## Migration Notes

- All new tables are additive.
- Financial data tables use append-only or insert-only patterns for ledger/events/archive snapshots.
- `report_archive_snapshots` preserves immutable calculated summaries and returns existing snapshots instead of overwriting them.
- `customer_portal_requests` stores portal requests and result metadata, not plaintext credentials.

## Rollback Notes

- Code rollback is standard Git rollback.
- Database rollback should not drop financial, event, or archive tables in production because they may contain records. Prefer forward migrations that mark sections inactive if needed.
- No financial report, ledger, or event data should be deleted as rollback.

## Provider Configuration Notes

- Notification providers are not live. Future SMS/WhatsApp/Telegram/email work must:
  - store secrets outside Git,
  - enforce provider status,
  - keep delivery audit records,
  - retry safely,
  - never expose token values in UI/logs.

## Portal Walled-Garden Notes

For subscriber/card portals to work for expired users inside a captive network, MikroTik must allow the portal URL through the walled garden.

Current module-local portal URLs:

- `/admin/radius/portal/subscriber/login`
- `/admin/radius/portal/subscriber`
- `/admin/radius/portal/card/login`
- `/admin/radius/portal/card`

Future production work should move these to a non-admin public URL/blueprint before customer rollout.

## Security Review

- Customer portal templates are standalone and do not render admin navigation.
- Customer portal data is scoped to the authenticated subscriber or card user session.
- Admin routes remain protected by the existing radius login guard.
- No hardcoded SMS/WhatsApp/Telegram provider secrets were added.
- No finance delete routes were added.
- Ledger/event/archive semantics remain append-only.

## Test Results

Final P15 validation results are recorded in `docs/handoffs/P15_CODEX_FINAL_RELEASE_HANDOFF.md`.

## Known Release Risks

- Full `python -m pytest -q` may still be too long for a single local run; use targeted suites plus scheduled CI.
- Portal URLs are currently under the radius admin prefix.
- Customer portal credential model relies on existing subscriber/card credential storage.
- Debt lifecycle is incomplete.
- Real provider integrations are not configured.
- Live speed/CoA apply is not implemented.

## Recommended Next Phase

1. Flutter parity for the API and portal/report models.
2. Production deployment rehearsal with migrations and rollback planning.
3. Guarded live RADIUS/CoA apply hardening.
4. Provider integrations with secure secret management.
