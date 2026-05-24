# Setup Wizard Internal Pilot Drill

## Purpose

This drill prepares HobeRadius operators to test the Setup Wizard against a lab MikroTik/CHR without exposing customers to unsafe live automation. The drill proves inventory parsing, risk analysis, dry-run operation queues, rollback readiness, and diagnostics clarity.

## Lab Requirements

- Dedicated CHR or test MikroTik, not a customer router.
- Console or out-of-band access that does not depend on the WAN being changed.
- Fresh backup/export before every drill.
- HobeRadius running with `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY` unset or false for all preview and dry-run checks.
- Only enable live apply manually in a controlled lab step after reviewing dry-run and rollback output.

## Recommended CHR Topology

- `ether1`: simulated WAN or management uplink.
- `ether2`: spare LAN/test client port.
- `ether3`: Hotspot candidate.
- `ether4`: PPPoE/Broadband candidate.
- `hr-wg`: WireGuard test tunnel name when VPN bootstrap is being drilled.

## Before-Test Backup Commands

Run these manually on the lab router before any live test:

```routeros
/export file=hoberadius-before-drill
/system backup save name=hoberadius-before-drill
```

Copy the files off the router before continuing.

## Safe Order

1. Inventory only: paste `/interface`, `/ip address`, `/ip route`, `/ip pool`, `/ip firewall nat`, `/radius`, `/ip hotspot`, `/ppp`, and `/interface wireguard` print outputs into the wizard.
2. Dry-run internet: generate and dry-run the internet step, then inspect operation count and validation commands.
3. Dry-run VPN/RADIUS: generate and dry-run VPN/RADIUS only after internet verification.
4. Verify diagnostics: intentionally test missing handshake, missing RADIUS entry, and blocked API states in the lab.
5. Apply one isolated step only when manually enabled in lab: set `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=true`, use the exact confirmation phrase, and observe operation logs.
6. Rollback drill: rollback only generated tagged objects, then verify router state and restore backup if needed.

## Hard Stop Conditions

- No out-of-band access.
- No fresh backup/export.
- Dry-run contains untagged commands.
- Dry-run contains broad remove, disable, reset, import, fetch, or broad `set [find]`.
- WAN or VPN interface appears in Hotspot/Broadband selected interfaces.
- Candidate subnet overlaps WAN, VPN, Hotspot, or PPPoE subnet.
- Rollback preview includes untagged or broad remove.
- Any diagnostic is unclear enough that an operator cannot explain the next action.

## Do Not Test On Customer Routers

- Live apply.
- Rollback.
- WAN reconfiguration.
- PPPoE server creation.
- Hotspot bridge/port moves.
- Any firewall/NAT/routing changes.

Customer routers may be used later only after the CHR pilot passes, operator runbooks are reviewed, and backups plus out-of-band access are confirmed.
