# Web Flutter Parity Matrix

This file is the working source for matching the Flask web admin and the
Flutter Android/Windows client. Flutter must only call Flask JSON APIs. It must
not duplicate business rules, access the database directly, or call RADIUS /
MikroTik directly.

Status values:

- `done`: Web, API, Flutter, and tests exist for the current scope.
- `partial`: Some layers exist, but important actions or fields are missing.
- `dry_run`: The user can preview the operation, but it is not applied live.
- `missing`: No usable Flutter/API parity yet.
- `web_only_until_api`: Web exists, but there is no safe JSON API yet.
- `planned_disabled`: Intentionally disabled until the backend integration is real.

## Current Baseline

| Domain | Web Route | API Endpoint | Flutter Route / Screen | Status | Notes / Tests Needed |
| --- | --- | --- | --- | --- | --- |
| Dashboard counters | `/admin/radius/` | `/api/v1/dashboard` | `/` dashboard | partial | Flutter exists, but counters must be verified against real API data on each VPS. |
| Subscribers | `/admin/radius/users` | `/api/v1/accounts` | `/subscribers` | done | Create/edit/archive/enable/disable/extend flows are API-backed. |
| Subscriber finance | `/admin/radius/users/<username>/finance` | `/api/v1/payments`, `/api/v1/loans`, `/api/v1/ledger` | `/subscribers/<username>/finance`, `/ledger` | partial | Apply-to-RADIUS has dry-run/live result fields; needs more manual VPS verification. |
| Plans / offers | `/admin/radius/plans` | `/api/v1/profiles` | `/plans` | partial | Advanced plan fields exist; inline speed-rule editing must be rechecked on mobile. |
| Card batches | `/admin/radius/cards/batches` | `/api/v1/cards/batches` | `/cards`, `/cards/batches/<id>` | partial | API-backed list/detail/actions exist; Flutter layout and operational counters need more polish. |
| Card checker / operations | `/admin/radius/cards/checker` | `/api/v1/cards/check`, `/api/v1/cards/<id>/...` | `/cards/checker` | partial | Real API-backed operations exist; no password exposure. Needs final mobile visual polish. |
| Online sessions | `/admin/radius/sessions` | `/api/v1/sessions/online`, `/api/v1/sessions/disconnect` | `/sessions` | partial | Disconnect is API-backed; must verify against real CoA/NAS on VPS. |
| NAS / devices | `/admin/radius/devices` | `/api/v1/nas`, `/api/v1/devices`, `/api/v1/devices/sync` | `/nas` | partial | NAS CRUD exists. Device fingerprints browser/sync needs Flutter screen parity. |
| Admins / roles | `/admin/radius/admins`, `/admin/radius/roles` | `/api/v1/admins`, `/api/v1/roles` | `/admins`, `/roles` | done | Role editor is API-backed; permission enforcement still needs continuous testing. |
| Distributors | `/admin/radius/distributors` | `/api/v1/distributors` | `/distributors` | partial | API/UI exist; scoped visibility must stay covered by tests before new data views. |
| Audit log | `/admin/radius/audit` | `/api/v1/audit` | `/audit` | done | Flutter viewer exists; payload rendering should stay Arabic-friendly. |
| Financial reports | `/admin/radius/finance/reports` | `/api/v1/reports/*` | `/reports` | partial | Ledger-based foundations exist; immutable snapshot/export UX is not complete. |
| Recycle bin | `/admin/radius/recycle-bin` | `/api/v1/recycle-bin` | `/recycle-bin` | partial | Archive/restore exists for core domains; verify all old delete buttons archive safely. |
| Backups | `/admin/radius/backups` | `/api/v1/backups/status`, `/api/v1/backups/run` | `/backups` | partial | Local backup is real. Google Drive is intentionally disabled. |
| Bandwidth schedules | `/admin/radius/bandwidth-schedules` | `/api/v1/bandwidth-schedules` | `/bandwidth-schedules` | dry_run | Saved schedules and resolver exist. Live apply depends on backend flag and RADIUS adapter verification. |
| Print templates | `/admin/radius/print-templates` | `/api/v1/print-templates` | `/print-templates` | partial | Saved layout and preview exist. Real PDF/export renderer is not complete. |
| MikroTik configs | `/admin/radius/integrations/mikrotik` | `/api/v1/mikrotik` | missing | missing | API exists. Flutter config/test UI is required. |
| Webhooks | `/admin/radius/integrations/webhooks` | `/api/v1/webhooks/*` | partial | partial | Config/test/deliveries API exists; Flutter UI is still missing. |
| System status | `/admin/radius/status` | `/api/v1/system/status` | missing | partial | API exists; Flutter screen is next. |
| Diagnostics | `/admin/radius/diagnostics` | `/api/v1/system/diagnostics` | missing | partial | API exists; Flutter screen is next. |
| Sync queue | `/admin/radius/sync` | `/api/v1/system/sync`, retry/cancel | missing | partial | API exists; Flutter queue screen is next. |
| Reconcile | `/admin/radius/reconcile` | `/api/v1/system/reconcile` | missing | partial | API exists and runs backend reconciler; Flutter must show result clearly. |
| Settings | `/admin/radius/settings` | `/api/v1/settings` | missing | partial | API exists; Flutter screen is still missing. |
| API tokens | `/admin/radius/tokens` | `/api/v1/tokens` | missing | partial | API exists; token secret is shown once only on create. Flutter screen is still missing. |
| Tenants | `/admin/radius/tenants` | `/api/v1/tenants` | missing | partial | API exists; Flutter screen is still missing. |
| Operational reports | `/admin/radius/reports/*` | `/api/v1/operational-reports/<slug>` | `/operational-reports` | partial | JSON API and Flutter viewer exist for sessions, failed logins, login status, MAC history, profile changes, API messages, CoA failures, manager events, manager login status, and user events. Needs export/pinned filters later. |
| Tools: set speeds | `/admin/radius/tools/set-speeds` | missing | missing | web_only_until_api | Needs safe API. No direct Flutter RADIUS action. |
| Tools: maintenance | `/admin/radius/tools/maintenance` | missing | missing | web_only_until_api | API must require preview first and strong confirmation before run. |
| Tools: test auth | `/admin/radius/tools/test-auth` | missing | missing | web_only_until_api | Needs API-backed test screen. |
| Tools: RADIUS log | `/admin/radius/tools/radius-log` | missing | missing | web_only_until_api | Web has JSON helper; expose authenticated API before Flutter. |
| Bandwidth profiles | `/admin/radius/bandwidth-profiles` | `/api/v1/bandwidth-profiles` | missing | partial | API CRUD exists; Flutter screen is next. |
| Pools | `/admin/radius/pools` | `/api/v1/pools` | missing | partial | API CRUD exists; Flutter screen is next. |
| Vouchers | `/admin/radius/vouchers` | `/api/v1/vouchers` | missing | partial | API generate/list/revoke exists; Flutter screen is next. |
| Invoices | `/admin/radius/invoices` | `/api/v1/invoices` | missing | partial | API list/create/status exists; Flutter screen is next. No hard delete. |
| Tickets | `/admin/radius/tickets` | `/api/v1/tickets` | missing | partial | API list/create/update/reply exists; Flutter screen is next. |
| Services | `/admin/radius/services` | `/api/v1/services` | missing | partial | API CRUD exists; Flutter screen is next. |
| Share groups | `/admin/radius/share-groups` | `/api/v1/share-groups` | missing | partial | API CRUD/member management exists; Flutter screen is next. |

## Implementation Order

1. System operations API and Flutter screens:
   status, diagnostics, sync queue, retry/cancel, reconcile, RADIUS log.
2. Admin control APIs and Flutter screens:
   settings, API tokens, tenants, webhooks deliveries/config/test.
3. Operational reports APIs and Flutter screens:
   sessions, failed logins, MAC history, profile changes, CoA failures, manager
   events.
4. SaaS module APIs and Flutter screens:
   bandwidth profiles, pools, vouchers, invoices, tickets, services.
5. Share groups API and Flutter screens.
6. Existing screen completion:
   dashboard counters, card operations, batch operations, sessions disconnect,
   backups, speed schedules, print templates, and mobile layout polish.

## Acceptance Rules

- A Flutter action must call a real API endpoint.
- If the API returns `not_implemented`, Flutter must show it as unavailable,
  not as success.
- Destructive maintenance or cleanup operations require preview, explicit
  confirmation, and audit logging.
- Passwords and secrets must not be included in list/detail responses.
- Google Drive backup remains `planned_disabled` until OAuth/storage is real.
- Live bandwidth apply must clearly show whether the response is `dry_run` or
  actually applied through RADIUS/MikroTik.
