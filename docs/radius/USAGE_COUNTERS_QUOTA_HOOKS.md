# Usage Counters and Quota Hooks

P14 connects accounting sessions to read-only usage summaries and an advisory
quota decision service.

## Scope

Implemented:

- per-tenant usage summary;
- per-subscriber usage summary;
- per-plan/profile usage summary when subscriber mapping exists;
- per-NAS aggregation inside tenant summary;
- daily and monthly windows;
- advisory quota decisions: `allow`, `warn`, `block`.

Not implemented:

- paid accounting ledger;
- reseller settlement;
- CoA or disconnect;
- automatic auth rejection;
- live router changes;
- radius-module-admin changes;
- Flutter UI.

## APIs

- `GET /api/v1/accounting/usage/tenant?window=daily|monthly`
- `GET /api/v1/accounting/usage/subscribers/<username>?window=daily|monthly`
- `GET /api/v1/accounting/usage/plans/<plan_id>?window=daily|monthly`
- `POST /api/v1/accounting/quota/check`

Quota check body:

```json
{
  "username": "ali",
  "limit_bytes": 104857600,
  "window": "daily"
}
```

Response includes:

- `decision`: `allow`, `warn`, or `block`
- `enforced`: always `false` in P14
- `used_bytes`
- `limit_bytes`
- `remaining_bytes`

## Enforcement Position

P14 is advisory only. It does not change the policy engine or reject live
authentication. This is intentional because auth-path integration requires a
separate risk review and customer-visible behavior decision.

## Window Model

Daily windows match `acctstarttime` prefix `YYYY-MM-DD`.

Monthly windows match `acctstarttime` prefix `YYYY-MM`.

The source of truth is `radacct`.
