# Customer Service API Contracts

This is the API contract source for customer-requested operational RADIUS
services. It is intentionally backend-first: Flask remains the source of truth
for validation, permissions, database writes, RADIUS/MikroTik actions, audit,
and accounting. Web admin and Flutter clients should consume these contracts
instead of duplicating business logic.

Contract-only endpoints must return `501 not_implemented` until their storage
and service layer are real. They must never return fake success. Endpoints
marked `implemented`, `partial`, or `dry_run` below are backed by real Flask
routes and must keep using the same authenticated response envelope.

## Current Status Refresh

This document started as a contract reservation file. It is now aligned with
the current Web/Flutter parity matrix as of 2026-05-21. Several domains have
moved from `contract_only` to working foundations or partial implementations:
loans, payments, ledger, recycle bin, distributors, local backups, bandwidth
schedules, print templates, lifecycle retention, and ledger-based reports.

For product-level parity, also check
`docs/api/WEB_FLUTTER_PARITY_MATRIX.md`. The hard rule remains: a UI must not
show an operation as complete when the API returns `not_implemented` or when a
feature is intentionally `dry_run` / `planned_disabled`.

## Response Envelope

Implemented success:

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "request_id": "...",
    "version": "v1"
  }
}
```

Contract-only response:

```json
{
  "ok": false,
  "error": {
    "code": "not_implemented",
    "message": "loans API contract is reserved for the upcoming R2 loans foundation slice.",
    "details": {
      "domain": "loans",
      "operation": "create",
      "planned_slice": "R2 loans foundation",
      "required_work": ["loan records table"]
    }
  },
  "meta": {
    "request_id": "...",
    "version": "v1",
    "domain": "loans",
    "status": "planned"
  }
}
```

## Status Terms

- `implemented`: endpoint exists and performs real backend work.
- `partial`: endpoint performs real backend work, but the product flow still
  needs richer UX, export, old-delete audit, or VPS acceptance.
- `dry_run`: endpoint can preview safely; live apply is gated or requires VPS
  proof.
- `vps_acceptance_required`: endpoint/UI exist, but final correctness depends
  on a real NAS/RADIUS VPS.
- `planned_disabled`: intentionally visible as disabled until an integration is
  real.
- `contract_only`: route is reserved and authenticated, but returns 501.
- `planned`: documented only; no route yet because an existing endpoint covers
  the discovery need or the domain needs design first.
- `dangerous_until_foundation`: mutation must not be exposed until additive
  schema/service/audit safeguards exist.

## Domains

### 1. Loans / Subscriber Credit

Purpose: grant temporary internet activation, record who granted it, duration,
value, reason, and settle it later without mixing RADIUS actions with finance.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/loans` | GET | Bearer | implemented | Yes |
| `/api/v1/loans` | POST | Bearer | vps_acceptance_required | Yes; supports dry-run/live result fields through accounting apply flow |
| `/api/v1/loans/{loan_id}` | GET | Bearer | implemented | Yes |
| `/api/v1/loans/{loan_id}/settle` | POST | Bearer | implemented | Yes |

Request shape later:

```json
{
  "subscriber_username": "user123",
  "duration_value": 2,
  "duration_unit": "days",
  "value": 5.0,
  "currency": "JOD",
  "reason": "customer requested temporary access"
}
```

Remaining work: real VPS acceptance for activation/apply behavior, manager
approval workflow, richer max-limit policy controls, and customer-facing copy
that clearly distinguishes dry-run from live apply.

Flutter/Web impact: subscriber detail needs loan grant, loan history, and
settlement views.

### 2. Recycle Bin / Soft Delete

Purpose: stop losing sensitive operational history through hard delete.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/recycle-bin` | GET | Bearer | partial | Yes |
| `/api/v1/recycle-bin/{entity_type}/{entity_id}/archive` | POST | Bearer | partial | Yes; archives supported operational domains |
| `/api/v1/recycle-bin/{entity_type}/{entity_id}/restore` | POST | Bearer | partial | Yes; restores supported operational domains |

Remaining work: complete audit of older web delete paths, stronger UX acceptance,
and no automatic purge for sensitive records until backup/export safeguards are
real. See `docs/roadmap/DELETE_RISK_MAP.md`.

Flutter/Web impact: trash list, restore/cancel flows, visible archive status.

### 3. Accounting Ledger

Purpose: append-only commercial source of truth for sales, payments, debts,
discounts, settlements, distributor profit, and immutable reports.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/ledger` | GET | Bearer | implemented | Yes |
| `/api/v1/ledger/void` | POST | Bearer | implemented | Yes; creates reversal/void entries |

Remaining work: period-close policy, immutable snapshot/export UX, and final
financial-report acceptance against customer accounting expectations.

### 4. Payments / Partial Payments

Purpose: record subscriber payments, partial payments, discounts, custom prices,
and payment allocations without mutating financial history.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/payments` | GET | Bearer | implemented | Yes |
| `/api/v1/payments` | POST | Bearer | vps_acceptance_required | Yes; records payment and optional RADIUS apply result |
| `/api/v1/payments/{payment_id}/void` | POST | Bearer | implemented | Yes; uses void/reversal behavior |

Remaining work: real VPS acceptance for activation/apply behavior, richer
receipt/export UX, and stricter period-close semantics.

### 5. Distributor / Manager Scoped Operations

Purpose: support distributor users with scoped visibility, assigned batches,
sales ownership, debt/profit, and settlements.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/distributors` | GET | Bearer | partial | Yes |
| `/api/v1/distributors` | POST | Bearer | partial | Yes |
| `/api/v1/distributors/{distributor_id}/summary` | GET | Bearer | partial | Yes |
| `/api/v1/distributors/{distributor_id}/batches` | GET | Bearer | partial | Yes |
| `/api/v1/distributors/{distributor_id}/assign-batch` | POST | Bearer | partial | Yes |
| `/api/v1/distributors/{distributor_id}/settle` | POST | Bearer | partial | Yes; posts ledger-style settlement entry |

Remaining work: continuous enforcement tests for every sensitive API and deeper
debt/profit accounting acceptance.

### 6. Card Batches Advanced Operations

Purpose: manage batches/files with status counts, assignment, cancellation,
and later sales ownership.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/cards/batches` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/{batch_id}` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/{batch_id}/cards` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/import` | POST | Bearer | implemented | Yes; supports imported/external bookkeeping files |
| `/api/v1/cards/batches/export.csv` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/export.xlsx` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/export.pdf` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/generate` | POST | Bearer | implemented | Existing behavior |
| Batch bulk/archive/restore operations | POST | Bearer | partial | Existing supported actions only |

Remaining work: final operational UX acceptance and continued no-password
export tests.

### 7. Card Checker

Purpose: search a card and show existence, status, batch, profile, usage, and
known/missing fields without exposing the password.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/cards/check?query=...` | GET | Bearer | implemented | Yes |

Request: query parameter `query`, max 128 characters.

Response shape:

```json
{
  "ok": true,
  "data": {
    "card": {
      "exists": true,
      "status": "available",
      "username": "card123",
      "has_password": true,
      "batch": {},
      "profile": {},
      "missing_fields": ["sold_by"],
      "data_sources": ["cards", "card_batches", "access_plans"]
    }
  }
}
```

Error codes: `validation_error`, `unauthorized`, `rate_limited`.

### 8. Reports

Purpose: stable report contracts for date-range payments, activations, daily,
monthly, yearly sales, card sales, profit/loss, aging invoices, and distributor
debts.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/reports/sales` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/sales/daily` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/sales/monthly` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/sales/yearly` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/payments` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/loans` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/activations` | GET | Bearer | partial | Yes |
| `/api/v1/reports/card-sales` | GET | Bearer | partial | Yes |
| `/api/v1/reports/profit-loss` | GET | Bearer | partial | Yes |
| `/api/v1/reports/distributor-debts` | GET | Bearer | partial | Yes |
| `/api/v1/reports/<slug>/export.csv` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/<slug>/export.xlsx` | GET | Bearer | implemented | Yes |
| `/api/v1/reports/<slug>/export.pdf` | GET | Bearer | implemented | Yes |

Remaining work: customer acceptance of date-range/filter behavior.

### 9. Online Users / Live Sessions

Purpose: show current online users/cards and allow safe disconnect actions.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/sessions/online` | GET | Bearer | implemented | Yes |
| `/api/v1/sessions/disconnect` | POST | Bearer | implemented | Existing behavior |

Future work: richer card/subscriber filters and state color mapping.

### 10. Bandwidth Schedules

Purpose: time-based speed schedules now, pressure-based policies later.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/bandwidth-schedules` | GET | Bearer | dry_run | Yes |
| `/api/v1/bandwidth-schedules` | POST | Bearer | dry_run | Yes |
| `/api/v1/bandwidth-schedules/effective` | GET | Bearer | implemented | Yes |
| `/api/v1/bandwidth-schedules/{schedule_id}/apply` | POST | Bearer | dry_run | Yes; live apply is gated and needs VPS proof |

Remaining work: real VPS/NAS live-apply acceptance and clear UI copy whenever
the operation is only a dry run.

### 11. Card Print Templates

Purpose: store reusable card-print layouts with template options.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/print-templates` | GET | Bearer | partial | Yes |
| `/api/v1/print-templates` | POST | Bearer | partial | Yes |
| `/api/v1/print-templates/{template_id}/render` | POST | Bearer | partial | Yes; preview only |

Remaining work: real PDF/export renderer before showing PDF as complete.

### 12. Backup / Google Drive Readiness

Purpose: expose backup status/config contracts without adding OAuth or destructive
restore behavior yet.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/backups/status` | GET | Bearer | partial | Yes; local backup status is real |
| `/api/v1/backups/run` | POST | Bearer | partial | Yes; local backup run/verify is real |
| `/api/v1/backups/google-drive/connect` | POST | Bearer | planned_disabled | Yes; returns 501 intentionally |

Remaining work: real Google Drive OAuth/storage and restore-test flow. Until
then, the UI must show Google Drive as disabled, not successful.

### 13. NAS / Server Management Alignment

Purpose: manage NAS devices, test health, and keep server config aligned.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/nas` | GET/POST | Bearer | implemented | Existing behavior |
| `/api/v1/nas/{nas_id}` | GET/PATCH/DELETE | Bearer | implemented | Delete risk remains |
| `/api/v1/nas/{nas_id}/test` | POST | Bearer | implemented | Yes |

Future work: replace hard-delete with archive/disable and keep accounting
history intact.

### 14. Profile / Offer Advanced Settings

Purpose: expose service type, speed, quota, pool, daily/monthly limits, and
later loan eligibility per offer.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/profiles` | GET/POST | Bearer | implemented | Existing behavior |
| `/api/v1/profiles/{profile_id}` | GET/PATCH/DELETE | Bearer | implemented | Delete risk remains |

Future work: loan eligibility fields, apply-now/status endpoints, sync logs, and
policy enforcement tests for every advanced field.

## Discovery

`GET /api/v1/_routes` already lists registered API routes. It is authenticated
and is enough for this slice. The OpenAPI generator reads the Flask route map,
so no manual OpenAPI system is added here.
