# Setup Wizard Provisioning Orchestrator

## Purpose

The provisioning orchestrator turns the Setup Wizard from a script preview tool into a controlled router onboarding lifecycle. It reserves router identity, VPN addresses, peer names, API username plans, masked secret references, and prepared server-side WireGuard peer records.

This layer does not apply MikroTik configuration and does not mutate WireGuard server configuration. Server-side peer changes remain prepared and pending.

## State Machine

Router lifecycle states:

- `reserved`
- `script_generated`
- `waiting_router_key`
- `router_key_received`
- `peer_pending`
- `peer_ready`
- `vpn_verified`
- `radius_pending`
- `radius_verified`
- `api_pending`
- `api_verified`
- `fully_onboarded`
- `failed`
- `retired`

Transitions are forward-only except explicit retry from `failed` into a safe recovery state. `retired` is terminal.

## Provisioning Flow

1. Reserve or reuse the active registry row for the wizard run.
2. Allocate the next available router VPN IP from the configured pool.
3. Reserve peer/API names and masked secret references.
4. Generate the VPN/RADIUS router script preview.
5. Create a `prepared_wireguard_peers` row with status `waiting_router_key`.
6. The router script generates a router-side WireGuard keypair.
7. The operator pastes the router public key into Wizard V2.
8. The system validates format and duplicate usage.
9. The peer plan moves to `ready_to_apply`, and lifecycle moves to `peer_ready`.
10. Later verification steps can move lifecycle through VPN, RADIUS, and API verified states.

## 50 Router Scenario

With the default pool `10.10.0.0/24` and server IP `10.10.0.1`, router allocation starts at `10.10.0.2`.

Examples:

- Router 1: `10.10.0.2`, peer `hr-peer-0001`, API user `hr-api-0001`
- Router 25: `10.10.0.26`, peer `hr-peer-0025`, API user `hr-api-0025`
- Router 50: `10.10.0.51`, peer `hr-peer-0050`, API user `hr-api-0050`

Unique partial indexes prevent active duplicate IP allocations, active duplicate peer names, and active duplicate public keys.

## Failure Recovery

Supported recovery scenarios:

- Lost router script: reissue the same reserved allocation and peer plan.
- Interrupted wizard: resume from the latest registry lifecycle state.
- Duplicate public key: reject the key and keep the peer waiting.
- Peer collision: reject through unique active peer constraints.
- VPN verification failure: keep lifecycle recoverable and attach diagnostics.
- Failed reservation: mark failed, then retry or retire explicitly.

## Secret Handling

The registry stores masked or reference values only:

- `wireguard_private_key_ref`
- `radius_secret_ref`
- `api_password_ref`

The prepared peer stores router public keys only after operator submission. It exposes masked public-key values in summaries and UI.

## Future Guarded Apply Plan

Prepared server peers are intentionally not applied in this wave. A future guarded apply slice must:

- require lab/live flags
- consume `prepared_wireguard_peers`
- apply exactly scoped server peer changes
- record operations
- verify handshake
- offer rollback for generated tags only

Until that future slice exists, this system remains a lifecycle and readiness engine.
