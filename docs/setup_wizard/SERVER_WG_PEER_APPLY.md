# Guarded Server WireGuard Peer Apply

## Purpose

This layer moves a Setup Wizard router from a prepared peer record to a guarded server-side WireGuard peer apply plan. It is for controlled lab validation only and is not customer production automation.

The scope is the VPS/server side peer lifecycle only. It does not apply MikroTik configuration.

## Feature Flags

Server-side mutation is blocked unless both flags are enabled:

- `HOBERADIUS_SETUP_WIZARD_LAB_MODE=true`
- `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true`

Dry-run is allowed without the flags. Apply and rollback are blocked when either flag is absent or false.

## Flow

1. Router provisioning reserves VPN identity.
2. Operator submits router WireGuard public key.
3. Prepared peer reaches `ready_to_apply`.
4. Server peer dry-run creates:
   - config preview
   - exact `wg set` command preview
   - exact rollback preview
   - verification commands
5. Apply remains blocked unless flags and confirmation are present.
6. Verify parses `wg show` output and only marks VPN verified when the peer is observed with a handshake.
7. Rollback is scoped to the exact generated tag only.

## Safety Rules

Forbidden patterns:

- `wg-quick down`
- `systemctl restart`
- `iptables flush`
- `ip route flush`
- broad `sed` replacement
- deleting whole config
- untagged rollback
- broad peer reset

The generated tag shape is:

`HOBERADIUS_ROUTER:<registry_id> HOBERADIUS_SETUP:<run_id>:server-peer`

Rollback must include the same tag.

## Current Apply Capability

No real server mutation adapter is configured in this wave. Even with flags enabled, the default adapter returns:

`server_apply_adapter_not_configured`

Tests use a mock adapter only to prove operation sequencing, rollback scoping, and lifecycle behavior.

## CHR/VPS Lab Checklist

Before first real lab adapter:

1. Use a CHR router, not a customer router.
2. Keep secondary access to VPS and CHR.
3. Take `wg show` and server config snapshots.
4. Confirm the router public key and allowed IP are unique.
5. Run dry-run first.
6. Review rollback preview.
7. Enable flags only in the lab environment.
8. Apply one peer only.
9. Verify handshake using `wg show`.
10. Run rollback drill before production discussion.

## Hard Stop Conditions

- duplicate public key
- duplicate allowed IP
- missing router public key
- missing rollback preview
- stale or unknown server state
- any request to restart WireGuard service automatically
- any customer router as first test target

Production customer mode remains blocked until a dedicated, audited server write adapter is implemented.
