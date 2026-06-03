# Unified Design System — Rollout Tracker

Branch: `feature/unified-design-system` (worktree). Server: localhost:8062 (admin/Design#2026).

## Foundation (committed 5f810723)
Shared components + reference pages: plans_list, business_operators, users_list, account,
print_templates (export room), card_marketplace, embedded print modals.

## In-flight: card-editor internals
- [x] export-room real previews + collapsible strip + live-preview centerpiece
- [x] renderer color/alpha fidelity (decoration/strip)
- [x] text-wrap fix (mode-select cards)
- [ ] smooth direct-drag + position two-way sync (designer)
- [ ] sticky preview + segmented/tabbed sections (designer)
- [ ] add-logo upload + render (designer)
- [ ] PDF export double-download fix

## Rollout order
### Batch 1 — fragmented list/overview pages
cards_list, services_list, bandwidth_list, vouchers_list, tenants_list, admins_list,
tokens_list, tickets_list, roles_list, accounting_ledger, cards_overview, invoices_list,
sgrp_list, subscriber_groups_list, distributors_list, devices_list, sessions_list,
network_devices_list, plans_overview, reports_archive

### Batch 2 — custom-CSS pages
mt_alerts_index (.opsx-*), audit_log_index (.audit-*), subscribers_overview (.so-*),
events_*, finance_* hubs, operations_*

### Batch 3 — forms (switches + unit-inputs + card-select + left help + modal)
plans_form, users_form, bandwidth_form, sgrp_form, services_form, devices_form,
setup_wizard*, payment_collection_settings, finance_collection, network_devices_form,
network_policy_form, communications_*, roles_form, distributors_form, tenants_form,
invoices_form, admins_form

## Converted (verified 200 + markers + no errors)
- foundation 5f810723: plans_list, business_operators, users_list, account, print_templates,
  card_marketplace, cards_print_list, cards_batches, cards_recharge_list
- card-editor 1b0de4b1: print_templates designer (drag/sync/sticky/tabs/logo/double-download)
- 1a 5b74d3fa: cards_list, services_list, bandwidth_list
- 1b 918f657e: tenants_list, admins_list, tokens_list
- 1c dab872a5: tickets_list, roles_list, invoices_list(orphan)
- 1d 80d7bb02: distributors_list (+permissions_json dict guard), devices_list, network_devices_list
- 1e 88b7662c: sgrp_list, subscriber_groups_list, sync_list

## Exceptions / flagged
- vouchers_list.html, invoices_list.html — ORPHANED: routes 302-redirect to the billing hub
  (finance_billing.html). Convert the billing-hub tabs instead (batch 2), or delete as dead code.
- distributors_list: live page 500'd pre-existing (permissions_json stored as dict) — guarded in template.

- priority 9107c16a: rep_sessions(#3/#4 duration+RX readable), network_policy_form(#5 allowed-sites), mt_programming(#5)
- 2a 46a38363: communications, events_center, mt_alerts_index (stacked-hero collapse)
- 2b e18ef18e: subscribers_overview(.so), audit_log_index(.audit), finance_billing(billing hub, voucher-revoke->modal), finance_center(orphan)
- 3a ab5c7019: finance_center_hub(live finance hub), bandwidth_form, sgrp_form(switches+unit-inputs demo)

## Exceptions (cumulative)
- vouchers_list, invoices_list, finance_center — ORPHANED (live UI = finance_billing.html / finance_center_hub.html, both now converted)
- distributors_list — pre-existing dict-data 500, guarded
- mt_login_designer — deferred (heavy live-portal designer, handle on its own)
- bandwidth_form rate fields — left as-is (paired value+unit backend columns; unit-picker would break)

- spacing e63fab84: shared --uds-block-gap (20px) — consistent gap between ALL top-level blocks (fixes hero-glue)
- 3b 213c098a: cards_overview, plans_overview, reports_archive, sessions_list(/online)
- 3c 83b61b03: services_form, devices_form(switches), tenants_form(card-select)

- 3b/3c/4a–4e/mt-1/comm-1 (commits 213c098a..ab357453): overviews, all forms (services/devices/tenants/roles/distributors/admins), ALL report tables rep_* (13), reports_center, recycle_bin, mt_operations/problems/diagnostics, communications campaigns/templates/channels/deliveries

## CONVERTED COUNT: ~66 pages, 21 commits (a2d41837..HEAD)

## Additional ORPHANED (live UI elsewhere, already converted)
- finance_debts/finance_loans/finance_revenue/finance_wallets — routes 302 -> finance_center_hub (DONE)
- invoices_form/invoices_list/vouchers_list — billing hub (finance_billing, DONE)
- finance_center, mt_list (410 Gone) — dead legacy

## Remaining
- list/overview: cards_overview, plans_overview, reports_archive, reports_center, audit_list,
  sessions_list, customer_portals_admin, wh_deliveries, rep_* report tables, recycle_bin,
  company_inventory_expenses, payment_collection_*, lifecycle, etc.
- custom-CSS: mt_alerts_index(.opsx), audit_log_index(.audit), subscribers_overview(.so),
  finance_* hubs, events_*, operations_*, communications_*
- forms (switches+unit-inputs+card-select+left-help+modal): plans_form, users_form,
  bandwidth_form, sgrp_form, services_form, devices_form, setup_wizard*, *_form, finance_collection,
  network_policy_form, payment_collection_settings, etc.

---

## ✅ ROLLOUT COMPLETE — full-panel sweep finished (41 commits, a2d41837..HEAD)

Final parallel waves converted every remaining LIVE page. 182 of 199 radius templates
now carry UDS markers; the 17 without are all non-page files (nav include partials,
login/portal-login shells, mt_legacy_gone 410 stub) or the orphan below.

### Final batches (commits 36–41)
- mt-4: mt_dashboard (+human-readable RX/TX & uptime, NO data-uds-table on live-poll tbodies),
  mt_setup_form (cardselect+switches), mt_setup_script
- cards-3: cards_recharge_new/batch, cards_print_new/batch (353 lazy real thumbnails in batch modal)
- fin-tools: finance_accounting (accounting hub), tool_maintenance, tool_test_auth
- wizards: setup_wizard, setup_wizard_v2, setup_wizard_v3, setup_wizard_v3_router_service_flow
  (CONSERVATIVE hero/token-only — all wizard step JS / AJAX form names / data-* hooks untouched)
- npc+cards: network_policy_preview, network_policy_router_picker, cards_of_batch
- checker+detail+portal: cards_checker_v2 (live JS-heavy lookup — hero only, #cc-result &
  sessions table left to its own JS), audit_log_detail, portal_subscriber (standalone customer
  portal — injected UDS css/macros into its own <head>)

### Verified already-conformant (no edit needed)
- mt_login_designer — already uses hub.hero + card picker; live-preview iframe JS depends on
  .mt-designer-card/.is-active/[data-mt-designer-*], so cardselect/worklayout would BREAK it.
  Renders 200, hub-hero ×5, 23 designer hooks, 0 errors. Left as-is (careful solo pass = preserve).
- card_marketplace — bespoke per-offer marketplace accents (done earlier).

### ORPHANED / dead (NOT converted, unreachable)
- setup_wizard_fleet_compat.html — rendered by 0 routes (dead template).
- cards_checker.html — route renders cards_checker_v2.html; the .html stub is dead (edit reverted).
- finance_debts/loans/revenue/wallets, invoices_form/list, vouchers_list, finance_center,
  mt_list(410) — routes redirect/Gone; their live UIs (finance hubs / billing) already converted.

### Non-page templates intentionally untouched
- *_nav include partials (operations_nav, events_nav, reports_nav, communications_nav,
  network_ops_nav, data_protection_nav, roles_permissions_nav, admin_operations_nav) — these are
  nav fragments included by already-converted host pages, not standalone pages.
- login.html, portal_card_login.html, portal_subscriber_login.html — auth shells.

Tree clean. No merge/push performed. Branch: feature/unified-design-system.
