# Accounting Events Engine

P13 adds a small backend accounting-events layer over `radacct`.

## Scope

This layer normalizes accounting event payloads and updates `radacct` for:

- `Start`
- `Interim-Update`
- `Stop`
- `Accounting-On`
- `Accounting-Off`

It does not implement billing, reseller accounting, financial ledger entries,
CoA, disconnect, or quota enforcement.

## APIs

All routes use the existing API token guard.

- `POST /api/v1/accounting/events`
- `GET /api/v1/accounting/online`
- `GET /api/v1/accounting/sessions`
- `GET /api/v1/accounting/sessions/<session_id>`

## Normalized Fields

Accepted canonical fields:

- `username`
- `tenant_id`
- `acct_session_id`
- `acct_unique_session_id`
- `nas_ip_address`
- `calling_station_id`
- `framed_ip_address`
- `input_octets`
- `output_octets`
- `session_time`
- `status_type`

Common RADIUS-style names such as `Acct-Status-Type`,
`Acct-Session-Id`, `NAS-IP-Address`, `Acct-Input-Octets`, and
`Acct-Output-Octets` are also accepted.

## Session Behavior

- `Start` inserts one open `radacct` row.
- Duplicate `Start` for the same tenant/session/NAS is idempotent.
- `Interim-Update` updates counters and session time on the open row.
- `Stop` closes the open row.
- `Accounting-On` and `Accounting-Off` close all open rows for the NAS.
- Stale cleanup marks rows as `Stale-Session-Timeout`; it does not delete rows.

## Safety

FreeRADIUS remains the production accounting writer. This service is a backend
normalization foundation and a stable API layer for tests/future integration.

No auth, SQL-auth, CoA, MikroTik, RADIUS packet, ledger, or reseller behavior is
changed by this slice.
