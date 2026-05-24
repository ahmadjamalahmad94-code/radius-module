# Router Provisioning Registry

## Purpose

The Setup Wizard needs stable per-router connection data before any real CHR or
customer pilot can happen. The registry reserves that data without applying
anything to MikroTik, WireGuard, or RADIUS.

The registry is allocation-only:

- reserve a router identity for a wizard run
- reserve a unique router VPN IP
- reserve the server VPN IP used by the router
- reserve deterministic WireGuard peer/API names
- store only secret references or masked placeholders
- keep an IP allocation ledger for collision prevention

## Allocation Rules

Defaults:

- `HOBERADIUS_SETUP_WIZARD_VPN_POOL=10.10.0.0/24`
- `HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP=10.10.0.1`
- router allocations start at `.2`
- `.1` is always reserved for the server/VPS side

If the legacy WireGuard environment already defines `HOBERADIUS_WG_SUBNET` or
`HOBERADIUS_WG_SERVER_IP`, those values are used as fallbacks.

Example sequence:

| Router | Router VPN IP | Server VPN IP | Peer name | API username |
| --- | --- | --- | --- | --- |
| 1 | `10.10.0.2` | `10.10.0.1` | `hr-peer-0001` | `hr-api-0001` |
| 2 | `10.10.0.3` | `10.10.0.1` | `hr-peer-0002` | `hr-api-0002` |
| 50 | `10.10.0.51` | `10.10.0.1` | `hr-peer-0050` | `hr-api-0050` |

## Collision Prevention

Two tables are used:

- `router_provisioning_registry`
- `router_ip_allocations`

Active records are `reserved`, `generated`, `applied`, or `verified`.

SQLite partial unique indexes prevent active duplicates for:

- wizard run reservation
- router VPN IP
- allocation index
- IP allocation ledger entries

The same wizard run reuses its active reservation. Generating a fresh reservation
requires releasing the old one first.

## Secret Handling

This slice does not introduce encrypted secret storage. To avoid plaintext
secrets, the registry stores references such as:

- `radius-secret-ref-0001`
- `api-password-ref-0001`
- `wg-private-key-ref-0001`

The VPN/RADIUS preview can use the reference as a placeholder, but support
bundles and run summaries must keep sensitive values masked.

Future work should connect these references to the project's secure secret
storage before production live apply.

## Rollback and Release

Reservations may be released only while they are still safe lifecycle states:

- `reserved`
- `generated`
- `failed`

Release marks the registry row as `retired` and the allocation ledger as
`released`. Applied or verified routers are intentionally not released by this
service because they may represent real deployed infrastructure.

## Script Tags

VPN/RADIUS preview scripts include both:

- `HOBERADIUS_SETUP:<run_id>:vpn`
- `HOBERADIUS_ROUTER:<registry_id>`

The setup tag scopes the wizard step. The router tag ties generated objects back
to the provisioning registry.

## Future WireGuard Server Peer Apply Plan

This slice does not mutate the VPS WireGuard server. A future guarded lab/apply
slice should:

1. read the registry reservation
2. confirm a real router public key is available
3. dry-run a server-side peer operation
4. require feature flags and lab confirmation
5. apply one peer at a time
6. verify handshake
7. preserve rollback limited to generated tags

No customer production apply should be enabled until this path is tested in CHR
lab mode with support bundle evidence.
