# Capacity Status API

P05B adds a backend-only status surface for future Flutter/web capacity UX.
It is read-only and uses the last stored V40 capacity-contract snapshot plus
local usage counts.

## Endpoint

`GET /api/v1/system/admin-bridge/capacity-status`

Requires the normal API token. The endpoint does not call
`radius-module-admin`, does not create upgrade/payment/service requests, and
does not mutate RADIUS, MikroTik, FreeRADIUS, or CoA state.

## Response Shape

```json
{
  "ok": true,
  "data": {
    "status": "active|stale|degraded|unknown",
    "mode": "local_snapshot",
    "generated_at": "2026-05-25T00:00:00Z",
    "contract": {
      "status": "active|stale|unknown",
      "stale": false,
      "snapshot_id": 1,
      "fetched_at": "2026-05-25T00:00:00Z",
      "warnings": []
    },
    "usage": {
      "subscribers_total": 0,
      "cards_generated_month": 0,
      "nas_count": 0,
      "profiles_plans_count": 0,
      "print_templates_count": 0
    },
    "features": {
      "subscribers": {
        "state": "enabled|limited|locked|readonly|hidden|unknown",
        "usage_metric": "subscribers_total",
        "current_usage": 0,
        "limits": {"max_total": 100},
        "remaining": 100,
        "blocked": false,
        "block_code": null,
        "message_ar": "هذه الميزة ضمن الحدود المخزنة حالياً.",
        "upgrade_hint_ar": ""
      }
    },
    "warnings": [],
    "upgrade_intent": {
      "available": true,
      "mode": "local_intent_only",
      "dry_run_only": true,
      "message_ar": "..."
    }
  },
  "meta": {"request_id": "...", "version": "v1"}
}
```

## Supported Feature Keys

- `subscribers`
- `cards`
- `nas`
- `routers`
- `profiles`
- `print_templates`
- `admins`

## Safety Guarantees

- No secrets are returned.
- Raw license keys and shared secrets are not included.
- Raw capacity contract payload is not exposed.
- Missing contracts return `degraded` with `no_capacity_contract` and do not
  hard-block local runtime.
- Stale last-successful contracts return `stale` with `stale_contract`.
- Upgrade intent is local metadata only. It does not call admin, payment, or
  service-request APIs.

## Deferred Work

The original P05 Flutter UX remains deferred to a dedicated
`radius-module-app` wave. Flutter should consume this endpoint later while
continuing to treat backend enforcement errors as the source of truth.
