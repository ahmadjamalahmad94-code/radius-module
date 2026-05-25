# V40 Usage Metering

Prompt: P03 - Usage Metering Report to V40

This feature builds a normalized usage payload for the admin bridge. It is not
capacity enforcement and does not modify RADIUS, MikroTik, FreeRADIUS, CoA,
subscribers, cards, NAS devices, or accounting data.

## Metrics

The payload includes:

- subscribers total
- active subscribers
- cards generated total
- cards generated in current month
- active cards
- card batches
- NAS count
- routers count
- admins count
- profiles/plans count
- print templates count
- current online sessions from `radacct`
- SQLite database file size
- latest router backup timestamp when available
- app version/build from `HOBERADIUS_BUILD_SHA`

Missing optional tables return zero or empty values rather than errors.

## Manual Route

```text
POST /api/v1/system/admin-bridge/usage-report
```

Request:

```json
{
  "dry_run": true,
  "report_window": "2026-05"
}
```

The route is protected by the existing API token guard. It defaults to dry-run.

## Sending

Remote sending is disabled unless the V40 bridge environment is configured:

```text
HOBERADIUS_ADMIN_BRIDGE_ENABLED=true
HOBERADIUS_ADMIN_BASE_URL=https://admin.example
HOBERADIUS_LICENSE_KEY=...
```

The sender uses the admin bridge timeout and an idempotency key. Tests use a
mock transport and make no live admin calls.

## Persistence

Attempts are stored in `license_admin_usage_report_attempts` with:

- tenant id
- report window
- idempotency key
- dry-run flag
- status
- sanitized payload JSON
- sanitized error JSON
- sent timestamp

## Example Payload

```json
{
  "license_key": "lic_...6789",
  "instance_id": "",
  "module": "radius-module",
  "report_window": "2026-05",
  "generated_at": "2026-05-25T00:00:00Z",
  "app_version": "",
  "metrics": {
    "subscribers_total": 10,
    "subscribers_active": 8,
    "cards_generated_total": 100,
    "cards_generated_month": 20,
    "active_cards": 70,
    "card_batches": 4,
    "nas_count": 2,
    "routers_count": 2,
    "admins_count": 1,
    "profiles_plans_count": 5,
    "print_templates_count": 3,
    "current_online_sessions": 6,
    "db_storage_bytes": 123456,
    "last_backup_timestamp": "2026-05-25T00:00:00Z"
  },
  "idempotency_key": "<sha256>"
}
```

## Safety

- Dry-run is the default.
- No enforcement exists in this prompt.
- Admin failure is recorded but does not break the app.
- Payloads are sanitized before persistence.
- No `radius-module-admin` files were touched.
