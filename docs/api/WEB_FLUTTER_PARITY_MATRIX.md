# Web Flutter Parity Matrix

This file is the working source for matching the Flask web admin and the
Flutter Android/Windows client. Flutter must only call Flask JSON APIs. It must
not duplicate business rules, access the database directly, or call RADIUS /
MikroTik directly.

Status values:

- `done`: Web, API, Flutter, and tests exist for the current scope.
- `partial`: Some layers exist, but important actions or fields are missing.
- `dry_run`: The user can preview the operation, but it is not applied live.
- `vps_acceptance_required`: Code/API/UI exist, but the final proof requires
  a real customer VPS/NAS/RADIUS environment.
- `missing`: No usable Flutter/API parity yet.
- `web_only_until_api`: Web exists, but there is no safe JSON API yet.
- `planned_disabled`: Intentionally disabled until the backend integration is real.

## Current Baseline

| Domain | Web Route | API Endpoint | Flutter Route / Screen | Status | Notes / Tests Needed |
| --- | --- | --- | --- | --- | --- |
| Dashboard counters | `/admin/radius/` | `/api/v1/dashboard` | `/` dashboard | done | API exposes stable flat counter aliases and Flutter reads nested/flat shapes; live values still depend on each VPS database. |
| Subscribers | `/admin/radius/users` | `/api/v1/accounts` | `/subscribers` | done | Create/edit/archive/enable/disable/extend flows are API-backed. |
| Subscriber finance | `/admin/radius/users/<username>/finance` | `/api/v1/payments`, `/api/v1/loans`, `/api/v1/ledger` | `/subscribers/<username>/finance`, `/ledger` | vps_acceptance_required | Apply-to-RADIUS has dry-run/live result fields; final proof needs real VPS/RADIUS acceptance. |
| Plans / offers | `/admin/radius/plans` | `/api/v1/profiles` | `/plans` | partial | Advanced plan fields exist; inline speed-rule editing must be rechecked on mobile. |
| Card batches | `/admin/radius/cards/batches` | `/api/v1/cards/batches`, `/api/v1/cards/batches/import` | `/cards`, `/cards/batches/<id>`, `/cards/import` | partial | API-backed list/detail/actions/import exist on Web and Flutter. Real Excel/PDF export is still missing and must not be shown as complete. |
| Card checker / operations | `/admin/radius/cards/checker` | `/api/v1/cards/check`, `/api/v1/cards/<id>/...` | `/cards/checker` | vps_acceptance_required | Real API-backed operations exist; no password exposure. Live disconnect must be verified against NAS/CoA. |
| Online sessions | `/admin/radius/sessions` | `/api/v1/sessions/online`, `/api/v1/sessions/disconnect` | `/sessions` | vps_acceptance_required | List/search/type filters are API-backed. Disconnect requires real CoA/NAS acceptance on VPS. |
| NAS / devices | `/admin/radius/devices` | `/api/v1/nas`, `/api/v1/devices`, `/api/v1/devices/sync` | `/nas`, `/device-fingerprints` | vps_acceptance_required | NAS CRUD and device fingerprints browser/sync exist. Live DHCP/MikroTik sync must be accepted per customer VPS. |
| Admins / roles | `/admin/radius/admins`, `/admin/radius/roles` | `/api/v1/admins`, `/api/v1/roles` | `/admins`, `/roles` | done | Role editor is API-backed; permission enforcement still needs continuous testing. |
| Distributors | `/admin/radius/distributors` | `/api/v1/distributors` | `/distributors` | partial | API/UI exist; scoped visibility must stay covered by tests before new data views. |
| Audit log | `/admin/radius/audit` | `/api/v1/audit` | `/audit` | done | Flutter viewer exists; payload rendering should stay Arabic-friendly. |
| Financial reports | `/admin/radius/finance/reports` | `/api/v1/reports/*`, `/api/v1/reports/snapshots`, `/api/v1/reports/*/export.csv`, `/api/v1/reports/*/export.xlsx`, `/api/v1/reports/*/export.pdf` | `/reports` | partial | Ledger-based reports, immutable snapshots, CSV export, Excel export, and PDF export exist in Web/Flutter. Remaining work is customer acceptance of date-range/filter behavior. |
| Recycle bin | `/admin/radius/recycle-bin` | `/api/v1/recycle-bin` | `/recycle-bin` | partial | Archive/restore exists for core domains with lifecycle metadata. Remaining work is full old-delete audit and recycle UX acceptance. |
| Lifecycle retention | `/admin/radius/lifecycle` | `/api/v1/lifecycle/*` | `/lifecycle` | done | Policies, preview, manual run, events, and retention fields exist. Worker is opt-in via VPS env. |
| Backups | `/admin/radius/backups` | `/api/v1/backups/status`, `/api/v1/backups/run` | `/backups` | partial | Local backup is real. Google Drive is planned_disabled until OAuth/storage is real. |
| Bandwidth schedules | `/admin/radius/bandwidth-schedules` | `/api/v1/bandwidth-schedules` | `/bandwidth-schedules` | dry_run | Saved schedules and resolver exist. Live apply depends on backend flag and RADIUS adapter verification. |
| Print templates | `/admin/radius/print-templates` | `/api/v1/print-templates` | `/print-templates` | partial | Saved layout and preview exist. Real PDF/export renderer is not complete. |
| MikroTik configs | `/admin/radius/integrations/mikrotik` | `/api/v1/mikrotik` | `/mikrotik` | done | Flutter now supports list/create/edit/delete plus saved and unsaved connectivity tests. Saved passwords are not returned. |
| Webhooks | `/admin/radius/integrations/webhooks` | `/api/v1/webhooks/*` | `/admin-control` | done | Config save, test dispatch, and deliveries viewer are API-backed in Flutter. |
| System status | `/admin/radius/status` | `/api/v1/system/status` | `/system-operations` | done | Flutter shows backend counters, routers, and sync summary from the real API. |
| Diagnostics | `/admin/radius/diagnostics` | `/api/v1/system/diagnostics` | `/system-operations` | vps_acceptance_required | Flutter displays diagnostics; live router/API verdicts require VPS acceptance tests. |
| Sync queue | `/admin/radius/sync` | `/api/v1/system/sync`, retry/cancel | `/system-operations` | done | Queue list, retry, and cancel are API-backed with confirmation where needed. |
| Reconcile | `/admin/radius/reconcile` | `/api/v1/system/reconcile` | `/system-operations` | vps_acceptance_required | Flutter triggers backend reconcile and shows stats; real NAS side effects need VPS acceptance tests. |
| Settings | `/admin/radius/settings` | `/api/v1/settings` | `/admin-control` | done | Flutter can view and edit settings through the JSON API. |
| API tokens | `/admin/radius/tokens` | `/api/v1/tokens` | `/admin-control` | done | Flutter can create/revoke tokens; one-time token secret is shown only on create. |
| Tenants | `/admin/radius/tenants` | `/api/v1/tenants` | `/admin-control` | done | Flutter can list/create/update tenants with API-backed limits and status fields. |
| Operational reports | `/admin/radius/reports/*` | `/api/v1/operational-reports/<slug>` | `/operational-reports` | partial | JSON API and Flutter viewer exist for sessions, failed logins, login status, MAC history, profile changes, API messages, CoA failures, manager events, manager login status, and user events. Needs export/pinned filters later. |
| Tools: set speeds | `/admin/radius/tools/set-speeds` | `/api/v1/tools/set-speeds` | `/tools` | done | API supports dry-run and real plan speed updates; Flutter exposes both preview and apply. |
| Tools: maintenance | `/admin/radius/tools/maintenance` | `/api/v1/tools/maintenance/preview`, `/api/v1/tools/maintenance/run` | `/tools` | done | API requires preview token and explicit confirmation phrase before run; Flutter uses that flow. |
| Tools: test auth | `/admin/radius/tools/test-auth` | `/api/v1/tools/test-auth` | `/tools` | done | API-backed policy-engine test exists and Flutter shows the decision. |
| Tools: RADIUS log | `/admin/radius/tools/radius-log` | `/api/v1/tools/radius-log` | `/tools` | done | Authenticated API exposes radpostauth rows without passwords; Flutter viewer exists. |
| Bandwidth profiles | `/admin/radius/bandwidth-profiles` | `/api/v1/bandwidth-profiles` | `/saas-modules` | done | API CRUD exists; Flutter generic SaaS module screen covers create/list/delete where supported. |
| Pools | `/admin/radius/pools` | `/api/v1/pools` | `/saas-modules` | done | API CRUD exists; Flutter generic SaaS module screen covers create/list/delete. |
| Vouchers | `/admin/radius/vouchers` | `/api/v1/vouchers` | `/saas-modules` | done | API generate/list/revoke exists; Flutter exposes generation and revoke. |
| Invoices | `/admin/radius/invoices` | `/api/v1/invoices` | `/saas-modules` | done | API list/create/status exists; Flutter can mark paid. No hard delete. |
| Tickets | `/admin/radius/tickets` | `/api/v1/tickets` | `/saas-modules` | done | API list/create/update/reply exists; Flutter exposes create and reply. |
| Services | `/admin/radius/services` | `/api/v1/services` | `/saas-modules` | done | API CRUD exists; Flutter generic SaaS module screen covers create/list/delete. |
| Share groups | `/admin/radius/share-groups` | `/api/v1/share-groups` | `/saas-modules` | done | API CRUD/member management exists; Flutter exposes core create/list/delete. |
| External card files | `/admin/radius/cards/batches/import` | `/api/v1/cards/batches/import` | `/cards/import` | done | Web and Flutter can import `external` bookkeeping batches and `imported` batches through the real API. `external` never syncs to RADIUS; passwords are not displayed after import. |
| Google Drive backup | `/admin/radius/backups` | planned OAuth/storage API | `/backups` | planned_disabled | Must stay disabled until real OAuth/storage integration exists. |

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
