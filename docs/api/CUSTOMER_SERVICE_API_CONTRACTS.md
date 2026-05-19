# Customer Service API Contracts

This is the API contract source for customer-requested operational RADIUS
services. It is intentionally backend-first: Flask remains the source of truth
for validation, permissions, database writes, RADIUS/MikroTik actions, audit,
and accounting. Web admin and Flutter clients should consume these contracts
instead of duplicating business logic.

Contract-only endpoints must return `501 not_implemented` until their storage
and service layer are real. They must never return fake success.

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
| `/api/v1/loans` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/loans` | POST | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/loans/{loan_id}` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/loans/{loan_id}/settle` | POST | Bearer | contract_only | Yes, returns 501 |

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

Future work: loan tables, settlement rules, max limits, approval state,
permission checks, ledger posting, audit events.

Flutter/Web impact: subscriber detail needs loan grant, loan history, and
settlement views.

### 2. Recycle Bin / Soft Delete

Purpose: stop losing sensitive operational history through hard delete.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/recycle-bin` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/recycle-bin/{entity_type}/{entity_id}/archive` | POST | Bearer | dangerous_until_foundation | Yes, returns 501 |
| `/api/v1/recycle-bin/{entity_type}/{entity_id}/restore` | POST | Bearer | dangerous_until_foundation | Yes, returns 501 |

Future work: additive archive columns, restore rules, delete compatibility,
financial void/reversal policy. See `docs/roadmap/DELETE_RISK_MAP.md`.

Flutter/Web impact: trash list, restore/cancel flows, visible archive status.

### 3. Accounting Ledger

Purpose: append-only commercial source of truth for sales, payments, debts,
discounts, settlements, distributor profit, and immutable reports.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/ledger` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/ledger/void` | POST | Bearer | dangerous_until_foundation | Yes, returns 501 |

Existing overlap: `/api/v1/accounting` is implemented, but it reads RADIUS
`radacct` sessions only. It is not a financial ledger.

Future work: append-only ledger entries, source typing, void/reversal entries,
period-close policy, invariants tests.

### 4. Payments / Partial Payments

Purpose: record subscriber payments, partial payments, discounts, custom prices,
and payment allocations without mutating financial history.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/payments` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/payments` | POST | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/payments/{payment_id}/void` | POST | Bearer | dangerous_until_foundation | Yes, returns 501 |

Future work: payment table, allocation service, ledger posting, receipt/audit
records, void behavior.

### 5. Distributor / Manager Scoped Operations

Purpose: support distributor users with scoped visibility, assigned batches,
sales ownership, debt/profit, and settlements.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/distributors` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/distributors` | POST | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/distributors/{distributor_id}/summary` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/distributors/{distributor_id}/settle` | POST | Bearer | dangerous_until_foundation | Yes, returns 501 |

Existing overlap: admins/roles APIs exist, but distributor-specific scoping and
financial ownership are not implemented.

Future work: scope model, permission enforcement, assigned batch ownership,
debt/profit ledger accounts.

### 6. Card Batches Advanced Operations

Purpose: manage batches/files with status counts, assignment, cancellation,
and later sales ownership.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/cards/batches` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/{batch_id}` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/batches/{batch_id}/cards` | GET | Bearer | implemented | Yes |
| `/api/v1/cards/generate` | POST | Bearer | implemented | Existing behavior |
| Batch assignment/cancel endpoints | TBD | Bearer | planned | No route yet |

Future work: status taxonomy, assignment/reassignment endpoint, cancel/archive
batch behavior, distributor ownership, no hard-delete batch policy.

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
| `/api/v1/reports/sales` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/reports/payments` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/reports/activations` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/reports/card-sales` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/reports/profit-loss` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/reports/distributor-debts` | GET | Bearer | contract_only | Yes, returns 501 |

Future work: ledger-backed report service and immutable report semantics.

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
| `/api/v1/bandwidth-schedules` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/bandwidth-schedules` | POST | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/bandwidth-schedules/{schedule_id}/apply` | POST | Bearer | dangerous_until_foundation | Yes, returns 501 |

Future work: schedule table, overlap validation, timezone policy, worker logs,
router apply/revert safety.

### 11. Card Print Templates

Purpose: store reusable card-print layouts with template options.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/print-templates` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/print-templates` | POST | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/print-templates/{template_id}/render` | POST | Bearer | contract_only | Yes, returns 501 |

Future work: template table, versioning, safe renderer/export path, print tests.

### 12. Backup / Google Drive Readiness

Purpose: expose backup status/config contracts without adding OAuth or destructive
restore behavior yet.

| Endpoint | Method | Auth | Status | Safe now |
| --- | --- | --- | --- | --- |
| `/api/v1/backups/status` | GET | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/backups/run` | POST | Bearer | contract_only | Yes, returns 501 |
| `/api/v1/backups/google-drive/connect` | POST | Bearer | contract_only | Yes, returns 501 |

Future work: scheduler, credential storage policy, Google Drive OAuth/service
account decision, restore-test flow.

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
