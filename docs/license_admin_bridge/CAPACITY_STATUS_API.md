# Capacity Status API

P05B adds a backend-only status surface for future Flutter/web capacity UX.
It is read-only and uses the last stored V40 capacity-contract snapshot plus
local usage counts.

## Endpoint

`GET /api/v1/system/admin-bridge/capacity-status`

Requires the normal API token. The endpoint does not call
`radius-module-admin`, does not create upgrade/payment/service requests, and
does not mutate RADIUS, MikroTik, FreeRADIUS, or CoA state.

`POST /api/v1/system/admin-bridge/license-sync` can be used to pull the live
signed `/api/license/check` approval from the license panel and derive the
local capacity/services snapshot consumed by this status endpoint.

For the live license panel currently running at `https://hoberadius.com`, set:

```bash
HOBERADIUS_ADMIN_BRIDGE_ENABLED=true
HOBERADIUS_ADMIN_BASE_URL=https://hoberadius.com
HOBERADIUS_LICENSE_KEY=<license-key-from-license-panel>
HOBERADIUS_ADMIN_SHARED_SECRET=<same-value-as-LICENSE_CHECK_HMAC_SECRET-in-license-panel>
HOBERADIUS_SERVER_FINGERPRINT=<stable-server-fingerprint>
HOBERADIUS_INSTANCE_ID=<stable-instance-id>
HOBERADIUS_ADMIN_BRIDGE_WORKER=1
HOBERADIUS_ADMIN_BRIDGE_SYNC_INTERVAL_SECONDS=300
HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC=1
HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED=1
HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN=1
```

The local admin UI also exposes:

- `GET /admin/radius/license-file` - shows the configured panel URL, masked
  license/secret state, last runtime-contract snapshot, last identity-sync
  snapshot, services, limits, and manual sync buttons.
- `GET /admin/radius/account` - current admin account page. For accounts
  managed by the license panel, password changes are sent to
  `https://hoberadius.com/api/integration/hoberadius/customer-users/password-change`
  over signed HTTPS, then identity sync refreshes the local hash/version.

Password authority is bidirectional in flow but centralized in storage: the
request may start in RADIUS, but the license panel hashes the new password and
increments `password_version`. RADIUS never stores or sends raw passwords except
inside that one HTTPS signed password-change request.

Manual first sync:

```bash
curl -X POST \
  -H "Authorization: Bearer <api-token>" \
  http://127.0.0.1/api/v1/system/admin-bridge/license-sync
```

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
    "license": {
      "status": "active|expired|suspended|revoked|denied|unknown",
      "active": true,
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
    "services": {
      "ip_change_vpn": {
        "enabled": true,
        "status": "active",
        "download_mbps": 50,
        "upload_mbps": 50,
        "max_vpn_users": 100,
        "enforcement_mode": "customer_runtime",
        "runtime_hint": "wireguard_tc_or_chr_queue"
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
