# CHR/VPS Setup Wizard Lab Evidence Template

Do not fill this file with real secrets, private keys, full private configs, or
customer data. Keep actual executed evidence in a dated copy:

```text
docs/setup_wizard/CHR_VPS_LAB_EVIDENCE_<YYYY-MM-DD>.md
```

If no real CHR/VPS lab run was executed, leave this template as pending and do
not fabricate outcomes.

## 1. Environment

| Field | Value |
| --- | --- |
| Evidence date | pending |
| Operator | pending |
| Lab location | pending |
| HobeRadius commit | pending |
| CHR version | pending |
| VPS OS/version | pending |
| WireGuard interface | pending |
| Test topology | pending |
| Customer data present? | no |

## 2. Feature Flags

| Flag | Value | Notes |
| --- | --- | --- |
| `HOBERADIUS_SETUP_WIZARD_LAB_MODE` | pending | required for lab server peer apply |
| `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY` | pending | required for lab server peer apply |
| `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS` | pending | required for readiness probing |
| `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER` | pending | required for real server adapter |
| `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY` | false | MikroTik live apply remains out of scope unless separately approved |

## 3. Wizard Run

| Field | Value |
| --- | --- |
| Wizard run id | pending |
| Registry id | pending |
| Router label | pending |
| Router identity | pending |
| Allocated router VPN IP | pending |
| Server VPN IP | pending |
| Peer name | pending |
| API username | pending |
| Secrets shown as masked refs only? | pending |

## 4. Script Checksums

Do not paste full scripts if they contain credentials or private material.

| Script | SHA-256 checksum | Tag confirmed | Notes |
| --- | --- | --- | --- |
| Internet preview | pending | `HOBERADIUS_SETUP:<run_id>:internet` | pending |
| VPN/RADIUS preview | pending | `HOBERADIUS_SETUP:<run_id>:vpn` | pending |
| Hotspot preview | pending | `HOBERADIUS_SETUP:<run_id>:hotspot` | pending |
| Broadband preview | pending | `HOBERADIUS_SETUP:<run_id>:broadband` | pending |

## 5. Inventory Evidence

| Item | Result |
| --- | --- |
| Inventory source | pasted / read-only probe / pending |
| WAN interface detected | pending |
| VPN interface detected | pending |
| Excluded interfaces shown | pending |
| Existing subnets detected | pending |
| Existing NAT rules detected | pending |
| Parser handled partial sections | pending |
| Secrets masked | pending |

Sanitized notes:

```text
pending
```

## 6. Server Readiness Output

Paste sanitized structured readiness output.

```json
{
  "status": "pending",
  "checks": {},
  "diagnostics": [],
  "next_action_ar": "pending"
}
```

## 7. Server Peer Dry-run Evidence

| Field | Value |
| --- | --- |
| Dry-run status | pending |
| Command preview exact-scoped | pending |
| Rollback preview exact-peer scoped | pending |
| Duplicate public key check | pending |
| Duplicate allowed IP check | pending |
| Blocking warnings | pending |

Sanitized command preview:

```text
wg set <interface> peer <masked-public-key> allowed-ips <router_vpn_ip>/32
```

Sanitized rollback preview:

```text
wg set <interface> peer <masked-public-key> remove
```

## 8. Backup Snapshot Before Apply

| Field | Value |
| --- | --- |
| `wg show` captured | pending |
| `wg showconf` captured | pending |
| PrivateKey masked | pending |
| Snapshot persisted | pending |
| Backup capture failure? | pending |

Masked backup excerpt:

```text
pending
```

## 9. Server Peer Apply Output

Only fill this if the real lab apply was executed.

| Field | Value |
| --- | --- |
| Confirmation phrase used | `APPLY SERVER PEER IN LAB` |
| Command executed as argv list | pending |
| `shell=False` confirmed | pending |
| Apply operation status | pending |
| Post-apply verify status | pending |
| Lifecycle state after apply | pending |
| Rollback suggested? | pending |

Sanitized result:

```json
{
  "status": "pending"
}
```

## 10. Masked `wg show` Evidence

Do not paste private keys. Shorten public keys unless full public key is needed
for lab-only troubleshooting.

```text
interface: <lab-interface>
peer: <public-key-prefix>...<suffix>
  allowed ips: <router_vpn_ip>/32
  latest handshake: pending
  transfer: pending received, pending sent
```

## 11. Handshake Health Evidence

| Field | Value |
| --- | --- |
| Peer health status | pending |
| Health score | pending |
| Handshake age | pending |
| RX bytes | pending |
| TX bytes | pending |
| Arabic recommendation | pending |
| Raw output hidden by default | pending |

## 12. Rollback Evidence

Only fill this if the real lab rollback was executed.

| Field | Value |
| --- | --- |
| Confirmation phrase used | `ROLLBACK SERVER PEER IN LAB` |
| Rollback command exact-peer scoped | pending |
| Peer absent after rollback | pending |
| Rollback operation status | pending |
| Lifecycle state after rollback | pending |

Sanitized rollback result:

```json
{
  "status": "pending"
}
```

## 13. Hotspot/Broadband Preview Evidence

| Field | Hotspot | Broadband |
| --- | --- | --- |
| Tested? | pending | pending |
| Interface selected | pending | pending |
| WAN/VPN excluded | pending | pending |
| Script generated | pending | pending |
| Dry-run generated | pending | pending |
| NAT scoped | pending | pending |
| Verification method | pending | pending |

## 14. Support Bundle Masking Review

| Secret class | Plaintext found? | Evidence |
| --- | --- | --- |
| RADIUS secret | pending | pending |
| API password | pending | pending |
| WireGuard private key | pending | pending |
| Full private config | pending | pending |
| Customer data | no | lab only |

Support bundle checksum:

```text
pending
```

## 15. Screenshots

Attach sanitized screenshots only.

| Screen | Captured? | Notes |
| --- | --- | --- |
| V2 run start | pending | pending |
| Inventory summary | pending | pending |
| VPN/RADIUS allocation | pending | pending |
| Server readiness | pending | pending |
| Dry-run preview | pending | pending |
| Apply result | pending | pending |
| Health result | pending | pending |
| Rollback result | pending | pending |
| Support bundle review | pending | pending |

## 16. Final Verdict

Choose one:

- `pass`: lab evidence complete, rollback verified, no secret leakage.
- `partial`: preview/readiness/dry-run passed, apply or rollback not executed.
- `fail`: a hard stop or unsafe result occurred.

Verdict:

```text
pending
```

Operator notes:

```text
pending
```

Next safe action:

```text
pending
```
