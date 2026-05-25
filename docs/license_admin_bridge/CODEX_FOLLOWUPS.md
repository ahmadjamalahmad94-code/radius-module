# V40 Admin Bridge Follow-Ups

P01 did not edit `radius-module-admin`. The items below must be confirmed on
the admin-panel side before later prompts wire usage, backup, restore, or
service activation flows.

## Endpoint Gaps / Ambiguities

1. Confirm whether the admin panel exposes:
   `POST /api/v40/integration/hoberadius/license/check`.
2. Confirm whether the admin panel exposes:
   `POST /api/v40/integration/hoberadius/capacity-contract`.
3. Define the canonical authentication mechanism:
   shared secret header, bearer token, signed request, or another scheme.
4. Define the canonical license key field:
   `license_key`, `instance_license_key`, or a different identifier.
5. Define heartbeat / usage endpoint path, method, payload, and retention
   semantics.
6. Define backup upload endpoint path, method, payload, maximum size, checksum,
   and encryption expectations.
7. Define restore request polling endpoint path, method, idempotency, and
   rollback expectations.
8. Define service activation polling endpoint path, method, statuses, and
   retry behavior.
9. Define canonical capacity fields and names:
   subscribers, NAS/routers, cards, distributors, tenants, and feature flags.
10. Define admin outage behavior expected by the business contract. P01 keeps
    local RADIUS operation alive and records snapshots only.

## Current radius-module Position

- Admin bridge defaults disabled.
- License and capacity checks are optional and caller-driven.
- No entitlement enforcement exists yet.
- No backup upload exists yet.
- No restore polling exists yet.
- No service activation polling exists yet.
- No health heartbeat exists yet.
