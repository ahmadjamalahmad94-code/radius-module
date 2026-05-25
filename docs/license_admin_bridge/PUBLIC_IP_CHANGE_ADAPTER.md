# Public IP Change Adapter

P10 adds the first service activation adapter for
`network.public_ip_change`.

## Status

Dry-run only.

No live MikroTik apply is implemented in this slice. The adapter validates the
requested job, produces a scoped RouterOS preview, records the plan in
`license_admin_service_activation_executions`, and reports a deterministic
status.

## Supported Contract

Accepted job keys:

- `service_key`: `network` or `public_ip_change`
- `action_key`: `network.public_ip_change`

Required payload:

- `router_id`, `nas_id`, `target_router_id`, or `target_router`
- `requested_public_ip`, `new_public_ip`, or `public_ip`

Optional payload:

- `router_label`
- `router_type`: `mikrotik` or `routeros`
- `wan_interface` or `egress_interface`
- `method`: `srcnat_to_addresses` or `site_exit_nat`

## Dry-Run Output

The plan includes:

- target router/NAS details;
- intended public IP/NAT method;
- scoped preview commands;
- rollback expectations;
- risk notes.

Example command preview:

```routeros
/ip firewall nat add chain=srcnat out-interface="ether1" action=src-nat to-addresses="8.8.4.4" comment="HOBERADIUS_ADMIN_BRIDGE:public-ip-change:pubip-1"
```

The comment tag is generated per activation reference:

```text
HOBERADIUS_ADMIN_BRIDGE:public-ip-change:<reference>
```

## Live Apply

Live apply is disabled in P10.

If a job is processed with `dry_run=false`, the adapter records:

```json
{
  "status": "failed",
  "error": {
    "code": "public_ip_change_live_apply_not_enabled"
  }
}
```

Future live apply must require a separate guarded implementation with:

- explicit feature flag;
- confirmed target device;
- fresh router backup/export;
- scoped generated tags;
- rollback preview;
- tests proving no broad MikroTik rewrite.

## Safety Guarantees

- No MikroTik session is opened.
- No RouterOS command is executed.
- No route/NAT/firewall state is mutated.
- Invalid payloads are recorded as failed, not guessed.
- Unsupported router types are rejected.
- Duplicate references are idempotent.
