# Setup Wizard CHR Lab Validation

## Purpose

CHR Lab Mode validates the Setup Wizard against real MikroTik conditions while keeping customer production automation blocked. The order is:

Preview -> Inventory -> Dry-run -> Controlled single-step apply -> Verify -> Rollback drill.

This mode is internal engineering validation only. It is not one-click setup and must never be the first test on a customer router.

## Required Lab

- MikroTik CHR or disposable test MikroTik.
- Fresh `/export` and `/system backup`.
- Secondary access path such as console, hypervisor console, or management network outside the WAN being changed.
- VPN test VPS if drilling VPN/RADIUS.
- Isolated test subnets for Hotspot and PPPoE.
- `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=false` by default.
- `HOBERADIUS_SETUP_WIZARD_LAB_MODE=false` by default.

Both flags must be explicitly enabled for a controlled apply or rollback drill:

```powershell
$env:HOBERADIUS_SETUP_WIZARD_LIVE_APPLY="true"
$env:HOBERADIUS_SETUP_WIZARD_LAB_MODE="true"
```

## Safe Execution Order

1. Collect inventory only.
2. Generate and dry-run internet.
3. Apply internet only in lab if the policy allows it.
4. Verify internet immediately.
5. Rollback internet and verify rollback.
6. Generate and dry-run VPN/RADIUS.
7. Apply VPN/RADIUS only.
8. Verify VPN/RADIUS immediately.
9. Rollback VPN/RADIUS if the drill requires it.
10. Hotspot and Broadband are tested one-by-one, never chained.

## Hard Stop Conditions

- WAN interface is uncertain.
- Out-of-band access is missing.
- Backup/export is missing.
- Router inventory snapshot is stale.
- Rollback preview is unavailable.
- Subnet conflicts exist.
- Dry-run includes untagged commands.
- Verification fails after an apply.
- Any operator cannot explain the diagnostics.

## What Lab Mode Blocks

- Multi-step apply.
- Chained onboarding.
- Apply without inventory.
- Apply without dry-run.
- Apply without rollback preview.
- Apply or rollback when either feature flag is false.
- Rollback of untagged objects.
- Rollback of operations that were not actually applied.

## Never Test First On Customer Router

Do not run live apply, rollback, WAN route changes, Hotspot bridge/port moves, PPPoE service creation, or NAT/firewall changes on a customer router until the CHR pilot has passed and the operator guide has been rehearsed end-to-end.
