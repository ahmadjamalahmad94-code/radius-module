# P01 V40 Contract Audit

## Scope

This audit defines the first `radius-module` side of the V40 admin bridge. It
does not enforce entitlements, upload backups, poll restore requests, poll
service activations, send heartbeats, or call MikroTik/FreeRADIUS live paths.

## P00 Baseline

P00 found a clean `radius-module` tree and no implemented V40 bridge surfaces.
The pre-existing unrelated dirty tree remains isolated in:

```text
stash@{0}: On main: pre-p00-existing-radius-module-dirty-tree-before-v40-bridge
```

## Required Environment Variables

| Variable | Required now | Default | Purpose |
| --- | --- | --- | --- |
| `HOBERADIUS_ADMIN_BRIDGE_ENABLED` | yes to call admin | `false` | Master opt-in for all admin-panel bridge requests |
| `HOBERADIUS_ADMIN_BASE_URL` | yes when enabled | empty | Base URL for the V40 admin panel API |
| `HOBERADIUS_LICENSE_KEY` | one of two | empty | Preferred instance/license identifier |
| `INSTANCE_LICENSE_KEY` | fallback | empty | Backward-compatible license key alias |
| `HOBERADIUS_ADMIN_SHARED_SECRET` | optional until admin contract is final | empty | Optional shared secret header for admin bridge requests |
| `HOBERADIUS_ADMIN_TIMEOUT_SECONDS` | no | `3.0` | Per-request timeout, clamped between `0.5` and `30` seconds |
| `HOBERADIUS_ADMIN_RETRY_COUNT` | no | `0` | Retry count, clamped between `0` and `3` |

The bridge is disabled unless `HOBERADIUS_ADMIN_BRIDGE_ENABLED` is truthy.

## Proposed Admin Endpoints

These endpoints are proposed contracts for the admin panel side. P01 implements
only the `radius-module` client skeleton and local snapshots.

| Contract | Method | Path | P01 behavior |
| --- | --- | --- | --- |
| License check | `POST` | `/api/v40/integration/hoberadius/license/check` | Optional call, mocked in tests |
| Capacity contract | `POST` | `/api/v40/integration/hoberadius/capacity-contract` | Optional call, mocked in tests |
| Heartbeat / usage | TBD | TBD | documented as missing |
| Backup upload | TBD | TBD | documented as missing |
| Restore polling | TBD | TBD | documented as missing |
| Service activation polling | TBD | TBD | documented as missing |

## License Check Request

```json
{
  "license_key": "<masked instance license>"
}
```

Headers:

```text
Accept: application/json
Content-Type: application/json
User-Agent: HobeRadius-AdminBridge/1
X-HobeRadius-Admin-Secret: <optional shared secret>
```

## License Check Response

Minimum accepted shape:

```json
{
  "status": "active",
  "valid": true,
  "limits": {
    "subscribers": 1000,
    "nas": 50
  },
  "expires_at": "2026-12-31T23:59:59Z"
}
```

Validation is intentionally conservative:

- `status` must be a non-empty string.
- `ok`, if present, must be boolean.
- `valid`, if present, must be boolean.
- `limits`, if present, must be an object.

Invalid payloads are persisted as `invalid_payload` snapshots and returned as a
safe error. They do not crash the app and do not enforce limits.

## Capacity Contract Response

Minimum accepted shape:

```json
{
  "status": "active",
  "contract": {
    "plan": "pilot"
  },
  "limits": {
    "routers": 50,
    "subscribers": 1000
  }
}
```

Validation is intentionally conservative:

- `status` must be a non-empty string.
- `ok`, if present, must be boolean.
- `contract`, if present, must be an object.
- `limits`, if present, must be an object.

## Local Snapshot Storage

P01 adds `license_admin_bridge_snapshots` through migration `065`.

It stores:

- `tenant_id`
- `snapshot_type`: `license_check` or `capacity_contract`
- `status`: `healthy`, `invalid_payload`, `timeout`, `unavailable`,
  `config_missing`, or future states
- `source_url`
- sanitized `payload_json`
- sanitized `error_json`
- `fetched_at`, `expires_at`, and `created_at`

It does not store plaintext shared secrets, API passwords, private keys,
RADIUS secrets, or full license keys.

## Runtime Safety

- No client call runs during Flask startup.
- Admin outage returns structured safe status.
- Timeout is mandatory.
- Retry count is bounded.
- Network transport is injectable and mocked in tests.
- `radius-module` continues operating when admin is disabled or unavailable.
- No RADIUS auth/accounting behavior changes in P01.
- No MikroTik/FreeRADIUS live path changes in P01.

## P01 Implementation

Added:

- `app/radius/services/admin_panel_client.py`
- `app/radius/db/migrations/065_license_admin_bridge_snapshots.sql`
- `tests/test_license_admin_bridge_client.py`

The implementation is a foundation only. It is not wired into entitlement
checks, UI warnings, background workers, backup upload, restore polling, or
service activation.

## P02 Readiness

GO for P02 if the admin endpoint gaps in `CODEX_FOLLOWUPS.md` are acceptable
as open contract items.
