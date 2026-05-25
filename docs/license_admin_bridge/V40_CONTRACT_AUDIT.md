# V40 Bridge Contract Audit

Prompt: P01 - V40 Contract Audit, Read-Only

This document audits the integration contract needed between `radius-module`
and `radius-module-admin` V40. It is documentation only. P01 does not add
runtime code, enforcement, backup upload, service activation polling, restore
polling, health heartbeats, Flutter changes, or live RADIUS/MikroTik behavior.

## Operating Model

- `radius-module-admin` is the commercial/license brain.
- `radius-module` is the operational brain.
- `radius-module-admin` remains read-only reference for this sequence.
- Any missing admin-side endpoint must be tracked in
  `docs/license_admin_bridge/CODEX_FOLLOWUPS.md`.
- The bridge must fail safe: an admin-panel outage must not break local RADIUS
  auth/accounting or live router operation.

## Shared Authentication Expectations

The exact admin-panel authentication scheme is not confirmed in `radius-module`.
The bridge should support one explicit server-to-server mechanism, likely one of:

- `Authorization: Bearer <admin bridge token>`
- `X-HobeRadius-Admin-Secret: <shared secret>`
- signed request headers with timestamp and idempotency key

Until the admin side is confirmed, every runtime prompt must keep admin calls
optional, time-bounded, mockable, and disabled by default.

## Timeout and Retry Policy

Recommended defaults for later implementation:

- connect/request timeout: 3 seconds
- retries: 0 by default, maximum 3
- exponential backoff only for idempotent requests
- no app-startup mandatory calls
- failed calls should persist a degraded/stale status, not crash runtime paths

## Environment Variables To Standardize

| Variable | Purpose | Notes |
| --- | --- | --- |
| `HOBERADIUS_ADMIN_BRIDGE_ENABLED` | Global opt-in for admin bridge calls | Default false |
| `HOBERADIUS_ADMIN_BASE_URL` | Base URL for V40 admin API | Required only when enabled |
| `HOBERADIUS_LICENSE_KEY` | Preferred instance license key | Do not log raw |
| `INSTANCE_LICENSE_KEY` | Compatibility fallback | Do not log raw |
| `HOBERADIUS_ADMIN_SHARED_SECRET` | Optional server-to-server secret | Scheme needs admin confirmation |
| `HOBERADIUS_ADMIN_TIMEOUT_SECONDS` | Request timeout | Clamp to safe range |
| `HOBERADIUS_ADMIN_RETRY_COUNT` | Retry count | Clamp to safe range |

## Endpoint Contracts

### POST /api/license/check

Purpose:

- Check the license state for the local HobeRadius instance.
- Produce a local license snapshot for status display and future enforcement
  foundations.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "instance_id": "<optional stable instance id>",
  "module": "radius-module",
  "version": "<optional module version>"
}
```

Expected response payload:

```json
{
  "ok": true,
  "status": "active",
  "valid": true,
  "license_key": "<masked or omitted>",
  "expires_at": "2026-12-31T23:59:59Z",
  "features": {},
  "limits": {}
}
```

Idempotency:

- Not required for a pure read/check, but request IDs are useful for tracing.

Risk level:

- Medium. A wrong response could misrepresent business status later, but P01
  does not enforce it.

Open questions:

- The prompt file names this path as `/api/license/check`, while earlier local
  P00 notes expected `/api/v40/integration/hoberadius/license/check`.
  Admin-side canonical path must be confirmed.

### POST /api/integration/hoberadius/capacity-contract

Purpose:

- Fetch the commercial capacity contract for the instance.
- Provide limits for later enforcement foundations.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "instance_id": "<optional stable instance id>",
  "current_usage": {
    "subscribers": 0,
    "nas": 0,
    "cards": 0
  }
}
```

Expected response payload:

```json
{
  "ok": true,
  "status": "active",
  "contract": {
    "plan": "pilot",
    "tier": "internal"
  },
  "limits": {
    "subscribers": 1000,
    "nas": 50,
    "cards": 5000
  },
  "stale_after_seconds": 86400
}
```

Idempotency:

- Not required for read-only contract fetch.

Risk level:

- Medium/high once enforcement exists. P01 does not enforce.

Open questions:

- Canonical capacity field names and units need admin confirmation.

### POST /api/integration/hoberadius/instance-ops/heartbeat

Purpose:

- Send instance health and bridge status to admin panel.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "instance_id": "<stable id>",
  "status": "healthy",
  "version": "<module version>",
  "metrics": {},
  "warnings": []
}
```

Expected response payload:

```json
{
  "ok": true,
  "status": "accepted",
  "server_time": "2026-05-25T00:00:00Z"
}
```

Idempotency:

- Include an idempotency key or heartbeat sequence when retries are added.

Risk level:

- Low/medium. Should never alter local RADIUS behavior.

Open questions:

- Endpoint availability and accepted metrics are unconfirmed.

### POST /api/integration/hoberadius/backups/upload

Purpose:

- Upload a backup artifact or backup metadata to the admin panel.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "backup_id": "<local backup reference>",
  "checksum_sha256": "<hex>",
  "size_bytes": 0,
  "metadata": {}
}
```

Expected response payload:

```json
{
  "ok": true,
  "reference": "<admin backup reference>",
  "status": "stored"
}
```

Idempotency:

- Required. Use backup checksum or explicit idempotency key.

Risk level:

- High. Backup artifacts may contain secrets and customer data. Encryption,
  masking, size limits, and retention rules must be confirmed before runtime
  upload exists.

Open questions:

- Upload transport, encryption, max size, retention, and secret handling are
  unconfirmed.

### POST /api/integration/hoberadius/backup-restore/poll

Purpose:

- Ask admin panel whether a restore request exists for this instance.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "instance_id": "<stable id>",
  "last_seen_reference": "<optional>"
}
```

Expected response payload:

```json
{
  "ok": true,
  "requests": [
    {
      "reference": "restore_123",
      "status": "pending",
      "backup_reference": "backup_123"
    }
  ]
}
```

Idempotency:

- Poll is read-only. Restore status updates require idempotency.

Risk level:

- High. Actual restore can destroy local state if mishandled. P01 does not
  implement restore.

Open questions:

- Request schema, operator confirmation requirements, and safe restore mode are
  unconfirmed.

### POST /api/integration/hoberadius/backup-restore/<reference>/status

Purpose:

- Report local restore progress or failure back to admin panel.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "status": "dry_run_ready",
  "message": "operator confirmation required",
  "result": {}
}
```

Expected response payload:

```json
{
  "ok": true,
  "status": "accepted"
}
```

Idempotency:

- Required per restore reference and status transition.

Risk level:

- Medium for status-only, high if coupled with live restore.

Open questions:

- Allowed restore status state machine is unconfirmed.

### POST /api/integration/hoberadius/service-activations/poll

Purpose:

- Poll admin panel for requested service activations such as plan upgrades,
  features, or integrations.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "instance_id": "<stable id>",
  "current_features": {}
}
```

Expected response payload:

```json
{
  "ok": true,
  "activations": [
    {
      "reference": "act_123",
      "service_key": "example",
      "status": "pending"
    }
  ]
}
```

Idempotency:

- Poll is read-only. Status updates require idempotency.

Risk level:

- Medium/high depending on the service. No activation should mutate RADIUS or
  MikroTik without a later explicit safe prompt.

Open questions:

- Service key list, payload schema, and safety gates are unconfirmed.

### POST /api/integration/hoberadius/service-activations/<reference>/status

Purpose:

- Report activation result back to admin panel.

Expected request payload:

```json
{
  "license_key": "<masked>",
  "status": "completed",
  "result": {},
  "error": null
}
```

Expected response payload:

```json
{
  "ok": true,
  "status": "accepted"
}
```

Idempotency:

- Required per activation reference and status transition.

Risk level:

- Medium for status-only, high if coupled with live operational changes.

Open questions:

- Allowed activation statuses and retry semantics are unconfirmed.

## First Bridge Milestones

1. License snapshot.
2. Capacity snapshot.
3. Usage report.
4. Health heartbeat.
5. Backup upload.
6. Restore polling.
7. Service activation polling.

## P01 Verdict

GO for P02 only if the open admin-side endpoint questions are accepted as
tracked follow-ups. P01 is documentation-only and does not add runtime bridge
behavior.
