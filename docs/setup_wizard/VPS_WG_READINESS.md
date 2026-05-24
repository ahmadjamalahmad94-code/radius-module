# VPS WireGuard Readiness

This readiness layer answers one narrow question:

> Is the VPS environment safe enough for a controlled lab WireGuard peer apply later?

It does not apply peers, write configuration, restart services, or mutate the
server. It is a read-only preflight contract.

## Feature flags

Readiness is off by default:

```text
HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=false
HOBERADIUS_SETUP_WIZARD_LAB_MODE=false
HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=false
```

`SERVER_WG_READINESS` only permits read-only readiness checks. It does not
enable apply.

Any future server-side mutation still requires both:

```text
HOBERADIUS_SETUP_WIZARD_LAB_MODE=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true
```

## What readiness checks

Configuration:

- WireGuard interface name.
- Server VPN IP.
- Expected listen port.
- Optional config path.
- Command runner mode.

Read-only environment:

- Whether `wg show <interface>` can be represented by the safe runner.
- Whether `ip addr show <interface>` can be represented by the safe runner.
- Whether the WireGuard interface appears in `wg show`.
- Whether the server VPN IP appears on the interface.
- Whether the listen port matches the expected value.

Safety prerequisites:

- Backup directory configured.
- Rollback strategy configured.
- Command timeout configured.
- Interface allowlist configured.

## Command runner contract

The default runner is disabled and executes nothing.

Commands are classified before they can be represented:

- `read_only`: `wg show`, `ip addr show`, `ip route show`, and read-only
  `systemctl is-active`.
- `write`: `wg set`, `ip addr add`, `ip route add`, and similar mutation
  commands.
- `dangerous`: `wg-quick down`, `systemctl restart`, `iptables flush`,
  `ip route flush`, `rm`, broad `sed -i`, reboot/shutdown.

This wave intentionally does not add a real shell runner. Tests use a mock
runner only.

## Lab prerequisites

Before a real adapter is considered in a later wave:

1. Confirm CHR/VPS lab only.
2. Confirm an out-of-band VPS access path.
3. Export current WireGuard state.
4. Configure a backup directory.
5. Configure a rollback strategy.
6. Configure an interface allowlist.
7. Keep customer production mode blocked.

## Hard stop conditions

Stop before server peer apply if any of these are true:

- Readiness status is `blocked`.
- The interface is missing or not allowlisted.
- The server VPN IP is not assigned.
- The listen port does not match.
- Backups or rollback strategy are missing.
- Any real command runner would need broad config edits or service restart.

## Future path

The next safe step is a lab-only real adapter proposal, still behind:

- readiness success,
- dry-run success,
- explicit confirmation,
- lab mode,
- server WG apply flag,
- rollback preview,
- post-apply verification.

Production customer automation remains blocked.
