# V40 Service Activation Agent

This document describes the P09 backend-only service activation polling
foundation in `radius-module`.

## Purpose

V40 may queue operational jobs for a HobeRadius instance. The local module now
has a safe polling surface that can:

- ask V40 for pending service activation jobs;
- persist each job by reference;
- route supported jobs through an explicit local adapter registry;
- mark unsupported jobs as `unsupported_service`;
- send a structured status callback to V40.

The default behavior is intentionally passive. No adapter is registered by
default, and no MikroTik, RADIUS, FreeRADIUS, VPS, or CoA mutation is performed
by this prompt.

## Admin Contract

Assumed V40 endpoints:

- `POST /api/integration/hoberadius/service-activations/poll`
- `POST /api/integration/hoberadius/service-activations/<reference>/status`

Expected poll response shape:

```json
{
  "items": [
    {
      "reference": "activation-123",
      "service_key": "network",
      "action_key": "network.public_ip_change",
      "payload": {}
    }
  ]
}
```

`items`, `jobs`, and `service_activations` are accepted as list keys to keep
the bridge tolerant while the admin contract is finalized.

## Local Persistence

Jobs are stored in `license_admin_service_activation_executions`.

The `(tenant_id, reference)` unique index makes polling idempotent: receiving
the same job twice returns the existing local row and does not execute the
adapter again.

Stored payload, result, and error JSON are sanitized with the bridge masking
helper before persistence.

## Adapter Registry

Adapters are explicit and local:

- `service_key`
- `action_key`
- `dry_run_supported`
- `execute(job, dry_run)`

If no adapter matches the job, the execution is recorded as:

```json
{
  "status": "unsupported_service",
  "error_json": {"code": "unsupported_service"}
}
```

This is deliberate. Unsupported or future paid services must not appear as
completed locally.

## Manual Poll Route

Local API:

- `POST /api/v1/system/admin-bridge/service-activations/poll`

The route is JSON-only and defaults to dry-run. If the admin bridge is disabled
or missing config, it returns a structured disabled/config-missing result rather
than failing the app.

## Safety Guarantees

- No live service activation is registered by default.
- No Public IP change adapter is implemented in P09.
- No MikroTik writes.
- No FreeRADIUS or RADIUS auth/accounting changes.
- No admin-panel code changes.
- Duplicate jobs are idempotent.
- Unsupported jobs are reported clearly.

## Next Slice

P10 may add the first dry-run adapter for `network.public_ip_change`. Live
apply must remain gated by explicit safety checks and existing guarded patterns.
