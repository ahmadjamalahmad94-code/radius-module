# V40 Bridge Codex Follow-Ups

This file tracks items that require `radius-module-admin` confirmation or
changes. `radius-module-admin` is read-only for this sequence and was not
modified.

## P01 Admin Endpoint Gaps

1. Confirm canonical license endpoint path:
   - Prompt file says `POST /api/license/check`.
   - Earlier local planning expected `/api/v40/integration/hoberadius/license/check`.
2. Confirm canonical auth scheme:
   - shared secret header
   - bearer token
   - signed request
   - or another admin-approved mechanism.
3. Confirm canonical license key field:
   - `license_key`
   - `instance_license_key`
   - or another identifier.
4. Confirm capacity contract endpoint exists:
   `POST /api/integration/hoberadius/capacity-contract`.
5. Define capacity field names and units:
   subscribers, NAS/routers, cards, tenants, distributors, features.
6. Confirm heartbeat endpoint exists:
   `POST /api/integration/hoberadius/instance-ops/heartbeat`.
7. Confirm backup upload endpoint exists:
   `POST /api/integration/hoberadius/backups/upload`.
8. Define backup upload limits:
   max size, checksum, encryption, retention, secret handling.
9. Confirm restore poll endpoint exists:
   `POST /api/integration/hoberadius/backup-restore/poll`.
10. Confirm restore status endpoint exists:
    `POST /api/integration/hoberadius/backup-restore/<reference>/status`.
11. Define restore state machine and required operator confirmations.
12. Confirm service activation poll endpoint exists:
    `POST /api/integration/hoberadius/service-activations/poll`.
13. Confirm service activation status endpoint exists:
    `POST /api/integration/hoberadius/service-activations/<reference>/status`.
14. Define service activation state machine, allowed service keys, retry rules,
    and idempotency expectations.

## P03 Usage Metering Follow-Ups

15. Confirm whether V40 admin exposes a usage report endpoint. P03 currently
    assumes:
    `POST /api/integration/hoberadius/usage-report`.
16. Confirm required usage payload fields, accepted metric names, and whether
    the admin panel expects usage reports to be standalone or folded into the
    heartbeat endpoint.

## P04 Capacity Enforcement Follow-Ups

17. Confirm the canonical capacity contract shape for feature states. P04
    supports both `features.<key> = "locked"` and
    `features.<key>.state = "locked"`, but the admin contract should choose one.
18. Confirm final limit field names for:
    - `subscribers.max_total`
    - `cards.generate_per_batch`
    - `cards.monthly_generated`
    - `nas.max_total`
    - `profiles.max_total`
    - `print_templates.max_active`
19. Confirm whether NAS and routers are separate limits in V40 or whether
    router count maps to the local `nas_devices` table.

## P11 Operations Event Follow-Ups

20. Confirm whether V40 admin exposes a canonical operations event callback
    endpoint for radius-module events. P11 kept events local because the prompt
    did not provide a confirmed path.
21. Define accepted event payload fields, severity values, idempotency key
    behavior, and retry policy for future event callback delivery.
