# Fleet Provisioning Dashboard

The Fleet Provisioning Dashboard gives operators a read-only overview of
Setup Wizard router onboarding across tens or hundreds of routers. It does not
apply MikroTik scripts, mutate VPS WireGuard state, or enable customer
automation.

## Purpose

- Track router provisioning registry entries.
- Show lifecycle progress and failures.
- Show VPN allocation usage.
- Highlight routers that need operator action.
- Provide safe links into recovery/resume workflows.

## Statuses

The dashboard groups routers by registry status and lifecycle state:

- `reserved`
- `waiting_router_key`
- `peer_ready`
- `vpn_verified`
- `radius_verified`
- `api_verified`
- `fully_onboarded`
- `failed`
- `retired`

## Health model

Fleet health is intentionally conservative:

- `healthy`: lifecycle reached VPN/RADIUS/API verification or full onboarding.
- `missing_handshake`: peer is ready/applied but not verified yet.
- `stale`: router is failed or needs operator review.
- `not_verified`: onboarding has not reached a verifiable state.

The dashboard avoids showing raw `wg show` output or full public/private keys.

## Filters

Operators can filter by:

- registry status
- lifecycle state
- retired visibility
- text search over label, identity, VPN IP, and peer name

## Operator workflow

1. Open `/admin/radius/setup-wizard/fleet`.
2. Review KPI cards and allocation usage.
3. Open `Action needed` routers first.
4. Use router details to inspect masked lifecycle context.
5. Resume or retire through RecoveryService-backed action endpoints only.

## 50-router scenario

The service aggregates 50 routers in a single query with latest peer and run
state joined into the dashboard rows. Allocation usage is calculated from the
configured Setup Wizard VPN pool and active `router_ip_allocations`.

## Safety

- No live apply is introduced.
- Resume delegates to the recovery engine.
- Retire delegates to the recovery engine and keeps IP reuse blocked unless a
  future explicit manual reuse workflow is added.
- Secrets are masked using the existing support bundle masking helper.
