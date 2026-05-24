# Setup Wizard Recovery Engine

The recovery engine helps an operator resume or repair an interrupted Setup
Wizard run without mutating MikroTik or VPS state. It reads the wizard run,
steps, provisioning registry, prepared WireGuard peer, operations, verification
results, and latest router inventory snapshot, then returns a safe next action.

## Recovery states

- `clean_resume`: no blocking issue was found; continue from the current step.
- `waiting_user_action`: the run needs an operator/customer action before moving on.
- `failed_verification`: a verification step failed and can be retried after fixing diagnostics.
- `partial_apply`: at least one operation applied and another failed; review rollback before continuing.
- `stale_inventory`: router inventory is too old for safe planning.
- `peer_key_missing`: the router WireGuard public key was not submitted yet.
- `duplicate_peer_conflict`: a peer key or allowed IP conflict was detected.
- `subnet_conflict`: planned service networks overlap with existing router networks.
- `unsupported_recovery`: recovery cannot be automated safely.
- `terminal_retired`: the router/run was retired and normal resume is blocked.

## Safe actions

- `resume`: returns the next safe step only.
- `retry_verification`: calls the existing read-only verification engine.
- `regenerate_script`: regenerates the VPN/RADIUS script using the same router allocation.
- `abandon_step`: records an operator reason; it does not delete data.
- `retire_router`: marks the run and registry retired. Existing IP allocations are not released for reuse.
- `repair_plan`: returns dry-run-only guidance for operator review.

## Blocked actions

The recovery engine does not perform live router repair, WireGuard server apply,
NAT changes, route changes, firewall changes, or credential writes. Any live
action must remain behind the existing guarded lab apply flows.

## Human review required

Human review is required for:

- partial apply with rollback available
- subnet overlap
- duplicate WireGuard peer conflicts
- credential reissue
- router retirement
- stale inventory before any dry-run or lab apply

## Examples

Peer key missing:

1. Open the recovery panel.
2. Submit the MikroTik-generated public key.
3. Re-run server peer dry-run and verification.

Failed VPN verification:

1. Review the Arabic diagnostic card.
2. Paste fresh verification output.
3. Use retry verification.

Partial apply:

1. Stop the flow.
2. Review applied tagged operations.
3. Use the guarded rollback drill only for `HOBERADIUS_SETUP` tagged objects.

## Support workflow

When recovery is unclear, download the support bundle from the V2 recovery
panel. It includes sanitized run, step, verification, operation, and snapshot
summaries with secrets masked.
