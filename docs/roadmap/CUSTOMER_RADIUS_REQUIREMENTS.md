# Customer RADIUS Requirements Roadmap

This is the canonical product-roadmap source for the operational HobeRadius
platform in `radius-module`.

This is not part of `radius-module-admin` and must not be implemented in the
central license panel. The license panel controls commercial subscriptions for
installations. The operational RADIUS platform controls subscribers, cards,
profiles, NAS, accounting, reports, and network actions.

## Evidence Inspected

- DB migrations: `app/radius/db/migrations/001_tenants_admins.sql` through
  `015_admins_rbac_ext.sql`.
- API routes: `app/api/v1/accounts.py`, `profiles.py`, `cards.py`, `nas.py`,
  `sessions.py`, `accounting.py`, `admins.py`, `audit.py`.
- Web routes/templates: `app/radius/routes/*.py`,
  `app/templates/radius/*.html`, `app/templates/admin/_admin_layout.html`.
- Services/repos: `app/radius/services/*.py`, `app/radius/db/repos/*.py`,
  `app/radius/integration/*.py`.
- Tests: `tests/test_api_*.py`, `tests/test_policy_engine.py`,
  `tests/test_mikrotik_*.py`, `tests/smoke_e2e.py`.

## Current Architecture Findings

- SQLite migrations are the current schema mechanism.
- API writes generally go through services/adapters, which keeps web/API paths
  closer together.
- Existing RADIUS/Router operations cover account/profile/NAS sync paths,
  FreeRADIUS translation, MikroTik adapter paths, online sessions, and CoA
  disconnect helpers.
- Accounting is currently mostly RADIUS accounting plus invoices/recharges.
  It is not yet a full commercial ledger.
- Some customer-sensitive resources currently use hard delete. This conflicts
  with recycle-bin and financial immutability requirements.

## Roadmap Domains

| Domain | Priority | Phase | Backend impact | API impact | Web admin impact | Flutter later | Risks | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Subscriber loans and credit | P0 | R2 | Add loan records, settlement records, max limits, actor/reason tracking, optional approval state | New loan grant/settle/list endpoints | Subscriber detail actions, loan history, settlement forms | Mobile/desktop loan grant and settlement flow | Mixing loan value with RADIUS activation can corrupt accounting if not separated | Ledger foundation, permission model, audit events |
| Recycle bin and soft delete | P0 | R1 | Add soft-delete/archive strategy for sensitive entities; keep financial data append-only | Backward-compatible delete behavior or new archive endpoints | Trash views, restore where safe, void/reversal for financial rows | Trash/restore UI later | Current hard deletes can lose subscribers/plans/NAS/admins/cards | Lifecycle policy constants, additive migration plan |
| Direct profile/offer/speed control | P0 | R2 | Extend AccessPlan policy application and router sync without session drop when supported | Profile patch fields already partial; add apply-now/status endpoints | Plan speed/quota editor and apply logs | Profile editor parity | Router support differs; wrong sync can disconnect users | Existing `profiles.py`, `plans_repo.py`, `sqlite_adapter.py`, MikroTik adapter |
| Time-based bandwidth schedules | P1 | R3 | New schedule tables/jobs, policy resolver, audit logs | CRUD schedules, preview active policy | Offer schedule UI, logs | Schedule editor | Timezone and overlapping schedule bugs | Worker scheduler, policy engine, profiles |
| Pressure-based bandwidth policies | P2 | R4 | Metrics input, threshold policy engine, automatic apply/revert | Policy CRUD and status | Policy monitoring UI | Later | Needs real load signals and rollback safety | Stable online/accounting metrics |
| Accounting and ledger | P0 | R2 | Append-only ledger, payments, partial payments, discounts, distributor debt/profit, settlement entries | Ledger/payments/reports endpoints | Accounting pages and immutable reports | Accounting module | Financial data corruption if implemented as mutable invoices only | Entity lifecycle policy, permissions, audit |
| Managers/distributors scoped permissions | P0 | R2 | Scope rules, assigned batches, sales ownership, distributor balance/debt | Scoped API filtering and assignment endpoints | Manager/distributor screens and batch assignment | Scoped client views | API currently notes permissions are not fully enforced | Existing admins/roles/RBAC tables and tests |
| Card batch/file management | P0 | R1/R2 | Batch stats, status taxonomy, assignment fields, owner/distributor | Batch detail/status/check endpoints | Batch dashboard with colors | Batch views | Existing batches have counts but not full status lifecycle | Cards repo/service, manager/distributor model |
| Reports | P0 | R2/R3 | Immutable report sources from ledger and accounting | Date-range report endpoints | Reports pages by type | Reports dashboards | Reports based on mutable rows can drift | Ledger append-only model |
| Card checker | P0 | R1B | Query cards, batches, plan, usage, subscriber/session details | `GET /api/v1/cards/check` or similar | Search/checker page | Quick checker screen | Must not leak data across tenant/scope | Cards repo, sessions/accounting, permissions |
| Card printing | P1 | R3 | Store print templates, template versions, batch binding | Template CRUD and render/export endpoint | Print designer and preview | Later desktop print UX | Layout complexity and browser print inconsistencies | Card batch stability |
| Google Drive backup | P1 | R4 | Scheduled backup jobs, OAuth/service credentials, status records | Backup status/config endpoints | Backup settings and logs | Status only later | Secret handling and restore safety | Deployment backup strategy |
| Online users list | P0 | Existing/partial | Online sessions from adapter; status coloring needs policy mapping | `GET /api/v1/sessions/online` exists | `/admin/radius/online` exists | Online view | Live data quality depends on MikroTik/FreeRADIUS adapter | Adapter health, accounting puller |
| NAS/server management | P0 | Existing/partial | NAS CUD, test status, credentials, adapter sync | `/api/v1/nas` and `/test` exist | devices pages exist | NAS management | Hard delete still risky | Soft delete policy |
| Offer/profile advanced options | P0 | Existing/partial | Many advanced plan fields exist; loan eligibility missing | `/api/v1/profiles` accepts many fields | plans form exists | Profile editor parity | Semantics not fully enforced in RADIUS policy | Policy engine, router sync |

## What Must Not Be Implemented In The License Panel

- Subscriber loans, balances, or settlements.
- Card generation/checking/printing.
- NAS, MikroTik, FreeRADIUS, CoA, sessions, online users.
- Operational accounting ledger and sales reports.
- Distributor scoped sales/debt workflows.
- Google Drive backup for customer operational data.

The license panel may only decide whether an installation license is active,
limited, or denied. It must not become a second operational RADIUS backend.

## Phase Plan

### R1A - Safe Foundation

- Add lifecycle and roadmap constants/helpers.
- Document traceability and safe deletion policy.
- No schema changes.
- No UI changes.
- No existing delete behavior changes yet.

### R1B - Card Checker And Deletion Policy Design

- Add card checker service/API with tests.
- Add a migration proposal for soft-delete fields.
- Identify exact hard-delete endpoints and replacement behavior.

### R2 - Ledger, Loans, And Scoped Managers

- Add append-only ledger tables.
- Add subscriber loan tables and settlement records.
- Add distributor sales/debt ownership.
- Add permission enforcement on API writes.

### R3 - Schedules, Reports, Printing

- Add time-based speed schedules.
- Add immutable reports from ledger.
- Add card print template storage and preview/render flow.

### R4 - Automation And Backups

- Add backup scheduling/status.
- Add pressure-based policy engine only after metrics are reliable.
