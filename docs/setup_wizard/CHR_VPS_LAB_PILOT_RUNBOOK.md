# CHR/VPS Setup Wizard Lab Pilot Runbook

## Scope

This runbook is for a controlled internal engineering pilot only. It validates
the Setup Wizard path against a disposable MikroTik CHR and a lab VPS.

This is not customer production automation. Do not run this first on a customer
router.

## Lab Objective

Validate this sequence:

1. Preview.
2. Inventory.
3. Dry-run.
4. Controlled single-step server WireGuard peer apply, only if all lab flags and
   VPS readiness checks pass.
5. Verification.
6. Rollback drill.
7. Support bundle masking review.

MikroTik live apply remains out of scope for this runbook unless a separate
guarded lab operation drill is explicitly approved.

## Prerequisites

- HobeRadius checkout at the recorded pilot commit.
- Disposable MikroTik CHR or isolated lab MikroTik.
- VPS dedicated to the lab or a safe test WireGuard interface on a VPS.
- Hypervisor or console access that survives WAN/VPN mistakes.
- A test client network isolated from customer traffic.
- Operator who can read RouterOS terminal output and reverse the lab if needed.
- No real customer data, credentials, private keys, or production RADIUS secrets.

## Required Feature Flags

Defaults must remain blocked.

For server-side WireGuard peer apply in lab only:

```text
HOBERADIUS_SETUP_WIZARD_LAB_MODE=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=true
HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=true
```

Keep MikroTik live apply disabled unless a separate guarded lab operation test
is explicitly in scope:

```text
HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=false
```

## VPS Safety Checklist

Complete before any apply attempt:

- VPS snapshot or backup exists.
- WireGuard interface under test is not carrying customer traffic.
- `wg show <interface>` succeeds in read-only mode.
- `wg showconf <interface>` succeeds and private keys will be masked in evidence.
- Backup directory or operation snapshot storage is available.
- Interface name is allowlisted in the Setup Wizard server WG readiness config.
- No duplicate public key exists.
- No duplicate allowed IP exists.
- Command runner timeout is configured.
- Real adapter flag is off until the exact lab apply step.

Hard stop if any item cannot be proven.

## CHR Safety Checklist

Complete before any router-side test:

- CHR is disposable or recoverable from hypervisor console.
- Out-of-band access is confirmed.
- `/export hide-sensitive file=hoberadius-before-pilot` created.
- `/system backup save name=hoberadius-before-pilot` created.
- Backup/export files copied off the CHR.
- WAN interface is identified and will not be selected for Hotspot/Broadband.
- VPN interface name is identified and will not be selected for Hotspot/Broadband.
- Candidate Hotspot/PPPoE interfaces are isolated lab interfaces.
- Candidate subnets do not overlap WAN, VPN, Hotspot, or PPPoE ranges.

Hard stop if out-of-band access or backup is missing.

## Exact Operator Steps

### 1. Baseline Checks

1. Confirm git commit hash.
2. Confirm feature flags are all false by default.
3. Run:

```powershell
python -m compileall app
python -m pytest tests/test_setup_wizard_db_isolation.py -q
```

Expected outcome: both pass.

### 2. Create Wizard Run

1. Open `/admin/radius/setup-wizard-v2`.
2. Start a new run.
3. Record:
   - run id,
   - operator,
   - timestamp,
   - selected lab router identity.

Expected outcome: run is created and starts at the internet step.

### 3. Internet Source Preview

1. Select the lab internet source:
   - DHCP,
   - PPPoE,
   - Static,
   - or VLAN.
2. Fill only lab values.
3. Generate the internet script preview.
4. Record the script checksum, not private values.
5. Do not apply through production automation.

Expected outcome: script matches selected source type and contains
`HOBERADIUS_SETUP:<run_id>:internet`.

### 4. Router Inventory

Paste sanitized outputs from the CHR:

```routeros
/interface print detail
/ip address print detail
/ip route print detail
/ip pool print detail
/ip firewall nat print detail
/radius print detail
/ip hotspot print detail
/ppp profile print detail
/interface wireguard print detail
/interface wireguard peers print detail
```

Mask secrets before storing evidence.

Expected outcome: inventory summary appears, WAN/VPN interfaces are excluded,
and no parser crash occurs on partial sections.

### 5. VPN/RADIUS Preview

1. Generate VPN/RADIUS script preview.
2. Record:
   - registry id,
   - router label,
   - router VPN IP,
   - server VPN IP,
   - peer name,
   - API username.
3. Confirm secrets are displayed only as masked values or references.

Expected outcome: no placeholder VPN IPs such as generic `10.10.0.3`; the
registry allocation is used.

### 6. Router Public Key Exchange

1. Run the router-side key generation step manually in the lab if required.
2. Paste only the router public key into the wizard.
3. Do not paste or store the router private key.
4. Confirm duplicate key detection is clean.

Expected outcome: prepared peer moves to ready-to-apply or peer-ready state.

### 7. Server WireGuard Readiness

1. Keep apply flags disabled.
2. Run readiness.
3. Record structured readiness output.
4. Enable lab-only server flags only after readiness is `ready` and the operator
   has reviewed backup status.

Expected outcome: readiness is either `ready` or blocks with diagnostics.

### 8. Server Peer Dry-run

1. Run server peer dry-run.
2. Record:
   - config preview,
   - command preview,
   - rollback preview,
   - warnings.
3. Confirm command is exactly scoped:

```text
wg set <interface> peer <router_public_key> allowed-ips <router_vpn_ip>/32
```

Expected outcome: dry-run passes and rollback preview targets the exact public
key only.

### 9. Optional Lab-only Server Peer Apply

Proceed only if all four server WG lab flags are true and readiness is `ready`.

Required confirmation phrase:

```text
APPLY SERVER PEER IN LAB
```

Expected outcome:

- Backup snapshot is captured and masked.
- Apply command runs as argument list with `shell=False`.
- Verify runs immediately.
- Result is either `applied_no_handshake` or `verified_handshake`.
- Lifecycle does not jump to fully onboarded.

Hard stop if backup capture or post-apply verification fails.

### 10. Handshake and Health

1. Wait up to the lab-defined interval for the CHR to connect.
2. Run WireGuard peer health check.
3. Record:
   - peer state,
   - handshake age,
   - RX/TX summary,
   - health score,
   - Arabic recommendation.

Expected outcome: no raw private config appears in primary UI or evidence.

### 11. Rollback Drill

Proceed only after an applied operation exists.

Required confirmation phrase:

```text
ROLLBACK SERVER PEER IN LAB
```

Expected outcome:

- Rollback command is exactly:

```text
wg set <interface> peer <router_public_key> remove
```

- Verify confirms the peer is absent.
- Lifecycle returns to a safe pre-apply state.
- Rollback result is persisted.

### 12. Hotspot or Broadband Preview

1. Choose either Hotspot or Broadband; do not test both at once.
2. Use interface picker and confirm WAN/VPN interfaces are blocked.
3. Generate script preview.
4. Run dry-run review.
5. Do not run live MikroTik apply in this pilot unless a separate approved lab
   operation drill is in scope.

Expected outcome: script is scoped and tagged, dry-run warnings are clear, and
NAT scope is limited to the selected service range.

### 13. Verification and Support Bundle

1. Use pasted-output verification where live probes are not configured.
2. Generate support bundle.
3. Review the bundle for secret leakage.
4. Confirm no plaintext:
   - RADIUS secrets,
   - API passwords,
   - WireGuard private keys,
   - full private configs.

Expected outcome: support bundle is useful and sanitized.

## Evidence Capture Table

| Evidence item | Required | Storage rule | Status |
| --- | --- | --- | --- |
| Commit hash | yes | plain | pending |
| Feature flags | yes | plain, no secrets | pending |
| Wizard run id | yes | plain | pending |
| Router identity | yes | lab-only name | pending |
| Allocated VPN IP | yes | plain | pending |
| Internet script checksum | yes | checksum only | pending |
| VPN/RADIUS script checksum | yes | checksum only | pending |
| Inventory summary | yes | sanitized | pending |
| Server readiness JSON | yes | sanitized | pending |
| Dry-run command preview | yes | public key may be masked | pending |
| Backup snapshot proof | yes before apply | private keys masked | pending |
| Apply output | if apply executed | sanitized | pending |
| `wg show` output | if verify executed | public key shortened | pending |
| Health result | if verify executed | sanitized | pending |
| Rollback output | if rollback executed | sanitized | pending |
| Support bundle review | yes | no secrets | pending |
| Screenshots | optional | no secrets | pending |

## Failure Triage Table

| Symptom | Likely cause | Safe next action |
| --- | --- | --- |
| Readiness disabled | flags intentionally off | keep blocked or enable only in lab |
| Missing `wg` command | VPS package missing or path unavailable | install/repair in lab, do not apply |
| Backup capture fails | permission or command issue | stop, fix backup path/permissions |
| Duplicate public key | peer already exists | do not apply, inspect existing peer |
| Duplicate allowed IP | allocation collision | stop, investigate registry and wg state |
| Apply succeeds but verify fails | command accepted but peer not readable | stop, rollback if peer state is uncertain |
| No handshake | router has not connected | inspect CHR script, endpoint, firewall, allowed IP |
| WAN selected for Hotspot | inventory/risk issue or manual override | stop, correct interface selection |
| Subnet overlap | unsafe service range | choose alternate range and rerun dry-run |
| Support bundle shows secret | masking defect | stop, fix masking before any pilot evidence is shared |

## Go/No-go Checklist

Go only if all are true:

- Backup/export exists for CHR.
- VPS snapshot/backup exists.
- Out-of-band access is confirmed.
- Readiness is `ready`.
- Dry-run has no blocking safety errors.
- Rollback preview exists and is exact-peer scoped.
- Feature flags are explicitly lab-only.
- MikroTik live apply remains off for this pilot.
- Operator understands rollback and hard stops.
- Evidence template is ready and contains no real secrets.

No-go if any hard stop condition appears.

## Final Operator Notes

Do not convert this pilot into customer automation. Passing this lab pilot means
the system is ready for the next controlled lab repetition, not production
one-click onboarding.
