# Instance Health Heartbeat

P06 adds a backend-only heartbeat foundation from `radius-module` to the V40
admin bridge.

## Endpoint

`POST /api/v1/system/admin-bridge/heartbeat`

The route defaults to dry-run:

```json
{"dry_run": true}
```

Remote sending requires:

- `HOBERADIUS_ADMIN_BRIDGE_ENABLED=true`
- `HOBERADIUS_ADMIN_BASE_URL`
- `HOBERADIUS_LICENSE_KEY` or `INSTANCE_LICENSE_KEY`
- optional `HOBERADIUS_ADMIN_SHARED_SECRET`

The admin endpoint path used by the backend client is:

`POST /api/integration/hoberadius/instance-ops/heartbeat`

## Payload Summary

The heartbeat payload includes:

- masked license key and instance id
- module name and build/environment metadata
- database type and basic read status
- FreeRADIUS status as `unknown` unless a safe read-only probe exists
- accounting table/session summary
- local backup summary
- database storage size
- in-process worker heartbeat snapshot
- last successful admin bridge snapshot/usage report
- warnings and generated timestamp

## Safety

- No service restarts.
- No shell commands.
- No MikroTik, RADIUS, FreeRADIUS, or CoA mutation.
- No calls during app startup.
- Failed remote sends are persisted and returned without breaking local app
  runtime.
- Payloads and responses are sanitized before storage.

## Scheduling

No scheduler hook was added in P06. The project has worker heartbeat utilities,
but this bridge heartbeat remains manual until a later prompt wires a scheduler
using an established safe project pattern.
