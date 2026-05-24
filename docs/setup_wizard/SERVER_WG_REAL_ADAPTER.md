# Lab-only WireGuard Server Apply Adapter

This adapter is the first real server-side WireGuard mutation path for the
Setup Wizard. It is intentionally lab-only and does not touch MikroTik.

It must never be used as customer production automation.

## Required flags

All flags must be enabled at the same time:

```text
HOBERADIUS_SETUP_WIZARD_LAB_MODE=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=true
```

Defaults remain false. If any flag is missing or false, apply and rollback are
blocked before any command runner is reached.

## Allowed commands

Read-only:

```text
wg show <interface>
wg showconf <interface>
ip addr show <interface>
ip route show
```

Apply:

```text
wg set <interface> peer <router_public_key> allowed-ips <router_vpn_ip>/32
```

Rollback:

```text
wg set <interface> peer <router_public_key> remove
```

Commands are executed as argument lists with `shell=False`. Broad shell strings
are refused by the real adapter.

Forbidden examples:

```text
wg-quick down
systemctl restart
iptables flush
ip route flush
rm
sed -i
reboot
```

## Backup before apply

Before apply, the adapter captures:

```text
wg show <interface>
wg showconf <interface>
```

The snapshot is stored in the apply operation result. `PrivateKey` lines are
masked before persistence. If snapshot capture fails, apply is blocked.

## Apply flow

1. Check all four flags.
2. Require exact confirmation:
   `APPLY SERVER PEER IN LAB`
3. Require a successful dry-run.
4. Require readiness status `ready`.
5. Inspect current peers.
6. Block duplicate public key or duplicate allowed IP.
7. Capture sanitized backup.
8. Execute the exact `wg set ... allowed-ips ...` command.
9. Verify peer presence and allowed IP.
10. Return:
    - `verified_handshake` if handshake is observed.
    - `applied_no_handshake` if peer exists but router has not connected yet.
    - `failed_verification` if peer or allowed IP is missing.

The lifecycle only advances to `vpn_verified` when a handshake is observed.

## Rollback flow

1. Check all four flags.
2. Require an applied operation.
3. Require exact confirmation:
   `ROLLBACK SERVER PEER IN LAB`
4. Execute exact public-key removal:
   `wg set <interface> peer <router_public_key> remove`
5. Verify the peer is absent.
6. Mark rollback as complete only after the peer is no longer visible.

Rollback never targets untagged or wildcard peers.

## Hard stop conditions

Stop immediately if:

- readiness is not `ready`,
- backup capture fails,
- duplicate public key exists,
- duplicate allowed IP exists,
- the interface is not allowlisted,
- command classification is not explicitly allowed,
- verification fails after apply,
- rollback cannot verify peer removal.

## Lab checklist

Use only on an isolated CHR/VPS lab:

1. Confirm secondary VPS access.
2. Confirm current WireGuard config is backed up externally.
3. Run readiness.
4. Generate peer dry-run.
5. Apply one peer only.
6. Verify.
7. Run rollback drill.

Production customer mode remains blocked.
