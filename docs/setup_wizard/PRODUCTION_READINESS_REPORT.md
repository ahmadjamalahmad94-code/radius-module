# Setup Wizard Production Readiness Report

Date: 2026-05-24
Scope: `radius-module` Setup Wizard only

## Executive Summary

The Setup Wizard has matured into a strong internal onboarding and lab-validation system. It can guide a router from preview planning through inventory, dry-run, verification, recovery, fleet visibility, and lab-only server WireGuard peer apply. It is not ready for unsupervised customer production automation.

Final verdict:

- Lab-ready for controlled CHR/VPS engineering pilots: yes, with strict checklist discipline.
- Customer-facing guided script-preview onboarding: conditionally ready for internal operators to supervise.
- Customer production one-click automation: no.
- MikroTik live apply in production: blocked by default and not production-ready.
- Server WireGuard peer apply: lab-only, feature-flagged, narrow-command adapter only.

The system is safest today when used as:

1. A guided Arabic V2 onboarding wizard.
2. A deterministic script planner.
3. A verification and diagnostics assistant.
4. A router provisioning registry and fleet visibility layer.
5. A lab-only server WireGuard peer apply test bed.

It should not be sold or exposed as fully automated customer provisioning until the blockers below are resolved.

## Completed Capabilities

- Setup Wizard V2 product flow:
  - Internet source selection and details.
  - Internet script preview and verification.
  - VPN/RADIUS provisioning allocation and script preview.
  - Router public-key exchange.
  - WireGuard peer preparation section.
  - Hotspot/Broadband guided planning.
  - Added services planning with supported/partial/unsupported states.
  - Recovery panel.
  - Advanced details collapsed by default.
- Engineering View remains available at `/admin/radius/setup-wizard`.
- Internet uplink planner:
  - DHCP, PPPoE, Static IP, VLAN.
  - Tagged generated objects.
  - Scoped NAT.
  - Validation commands.
- VPN/RADIUS provisioning:
  - Router provisioning registry.
  - Unique router VPN IP allocation.
  - Unique peer/API names.
  - Secret references/masked values instead of plaintext secret storage.
- Router lifecycle:
  - Reserved through fully-onboarded/failed/retired lifecycle states.
  - Prepared WireGuard peer model.
  - Recovery events.
- Verification:
  - Pasted-output verification.
  - Read-only/probe contract where available.
  - Arabic diagnostics.
  - WireGuard peer health scoring.
- Operations guardrails:
  - Dry-run operation queue.
  - Feature-flagged MikroTik apply/rollback path.
  - Operation logging.
  - Rollback restricted to generated tags.
- Server WireGuard:
  - Readiness service.
  - Safe command runner contract.
  - Lab-only real server peer apply adapter.
  - Exact allowed commands only.
  - Backup/snapshot before server apply.
  - Verify and rollback flows.
- Fleet dashboard:
  - Fleet metrics.
  - Allocation usage.
  - Action-needed routers.
  - Router detail view.
- Support/recovery:
  - Support bundle.
  - Recovery analyzer/service.
  - Resume, retry verification, regenerate, abandon, retire.

## Lab-Only Capabilities

These are intentionally not production customer features:

- MikroTik live apply:
  - Requires `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=true`.
  - Requires `HOBERADIUS_SETUP_WIZARD_LAB_MODE=true`.
  - Requires confirmation phrase.
  - Still depends on guarded adapter selection and dry-run.
- MikroTik rollback:
  - Requires the same live/lab flags.
  - Rollback may only target generated `HOBERADIUS_SETUP:<run_id>:<step>` tags.
- Server WireGuard real peer apply:
  - Requires all of:
    - `HOBERADIUS_SETUP_WIZARD_LAB_MODE=true`
    - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true`
    - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=true`
    - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=true`
  - Requires readiness status `ready`.
  - Requires dry-run.
  - Requires exact confirmation phrase.
  - Requires backup capture.
  - Only applies one peer.

## Production-Blocked Capabilities

- Customer one-click router provisioning.
- Production MikroTik write automation.
- Production server WireGuard config mutation.
- Bulk multi-router apply.
- Automatic server peer apply without operator lab mode.
- Automatic repair that mutates router/VPS state.
- Anti-sharing/anti-tethering service automation.
- Added services live apply outside guarded operation engine.

## Safety Guarantees Confirmed

### Feature Flags

- `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY` defaults off because `live_apply_enabled()` only returns true for explicit truthy env values.
- `HOBERADIUS_SETUP_WIZARD_LAB_MODE` defaults off.
- `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY` defaults off.
- `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS` defaults off.
- `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER` defaults off.

### Command Safety

- MikroTik operation safety is centralized in `OperationSafetyValidator`.
- Forbidden MikroTik patterns include broad remove/disable/reset/import/export/tool fetch/password/user add and broad `set [find]`.
- Apply operations must carry a generated setup tag unless they are explicitly permitted validation/global commands.
- Rollback remove commands must include the exact `HOBERADIUS_SETUP:<run_id>:<step>` tag and cannot use broad `[find]`.
- Server command classification blocks dangerous shell patterns.
- Server real adapter uses list arguments, `shell=False`, command classification, timeout, and output masking.

### Secret Handling

- Router provisioning stores secret references/masked placeholders, not plaintext secret values.
- Support bundle uses `mask_secrets()`.
- Fleet service masks router details.
- WireGuard private keys are masked from captured output.
- Public keys are masked in returned prepared-peer summaries.

### UI Safety

- Setup Wizard V2 hides engineering JSON/raw details in collapsed Advanced sections.
- Apply buttons remain disabled/guarded unless flags and readiness allow.
- Engineering View remains separate.
- Lab sections are labeled as internal lab mode.

## Migration Audit

The Setup Wizard migrations are additive and do not intentionally destroy data:

- `048_setup_wizard_foundation.sql`
  - Creates `setup_wizard_runs` and `setup_wizard_steps`.
  - Stores JSON/text state and secret references.
- `049_setup_wizard_operations.sql`
  - Creates operation log table.
  - Stores command previews, rollback commands, results, and safety warnings.
- `050_setup_wizard_router_snapshots.sql`
  - Creates sanitized router inventory snapshot cache.
- `051_setup_wizard_pilot_metadata.sql`
  - Adds planner/script/compatibility metadata to steps.
  - Uses additive `ALTER TABLE`.
- `052_router_provisioning_registry.sql`
  - Creates provisioning registry and IP allocation tables.
  - Adds unique indexes for active run/IP/index collision prevention.
  - Stores secret refs, not plaintext secrets.
- `053_router_provisioning_lifecycle.sql`
  - Adds lifecycle columns and lifecycle/prepared-peer tables.
  - Stores `server_private_key_ref`, not plaintext private key.
- `054_prepared_wireguard_peer_operations.sql`
  - Creates lab-only server peer operation log.
  - No plaintext private keys.
- `055_setup_wizard_recovery_events.sql`
  - Creates recovery/audit event table.
  - No plaintext secrets.

Known migration caveat:

- Some additive `ALTER TABLE` migrations depend on the project migration runner's idempotency semantics. The SQL itself does not use `IF NOT EXISTS` for each added column because SQLite support is limited.

## Route Map

All paths are mounted under `/admin/radius`.

| Method | Route | Purpose | Mutation | Flags | Risk |
| --- | --- | --- | --- | --- | --- |
| GET | `/setup-wizard` | Engineering View | no | none | low |
| GET | `/setup-wizard-v2` | Product V2 wizard | no | none | low |
| GET | `/setup-wizard/fleet` | Fleet dashboard page | no | none | low |
| GET | `/setup-wizard/fleet/data` | Fleet JSON summary | no | none | low |
| GET | `/setup-wizard/fleet/router/<id>` | Router detail | no | none | low |
| POST | `/setup-wizard/fleet/router/<id>/resume` | Delegated recovery resume | DB state only | none | medium |
| POST | `/setup-wizard/fleet/router/<id>/retire` | Delegated recovery retire | DB lifecycle only | none | medium |
| GET | `/setup-wizard/server-wg/readiness` | Server WG readiness | read-only/disabled by default | readiness flag for probing | medium |
| POST | `/setup-wizard/runs` | Create wizard run | DB only | none | low |
| GET | `/setup-wizard/runs/<id>` | Run summary | no | none | low |
| POST | `/setup-wizard/runs/<id>/internet-source` | Store internet inputs | DB only | none | low |
| POST | `/setup-wizard/runs/<id>/generate-internet-script` | Script preview | DB script preview only | none | medium |
| POST | `/setup-wizard/runs/<id>/verify-internet` | Verification | read/probe/parse only | adapter-dependent | medium |
| POST | `/setup-wizard/runs/<id>/generate-vpn-radius-script` | VPN/RADIUS plan | DB allocation/script only | none | medium |
| POST | `/setup-wizard/runs/<id>/router-public-key` | Submit router public key | DB peer state only | none | medium |
| POST | `/setup-wizard/runs/<id>/server-peer/dry-run` | Server peer dry-run | DB operation only | none | medium |
| POST | `/setup-wizard/runs/<id>/server-peer/apply` | Lab server WG apply | server WG mutation | all server WG lab flags | high |
| POST | `/setup-wizard/runs/<id>/server-peer/rollback` | Lab server WG rollback | server WG mutation | all server WG lab flags | high |
| POST | `/setup-wizard/runs/<id>/server-peer/verify` | Server peer verify | read-only/pasted output | readiness optional | medium |
| POST | `/setup-wizard/runs/<id>/server-peer/health` | Peer health | read-only/pasted output | readiness optional | medium |
| GET | `/setup-wizard/runs/<id>/server-peer/operations` | Server peer operations | no | none | low |
| POST | `/setup-wizard/runs/<id>/verify-vpn-radius` | VPN/RADIUS/API verification | read/probe/parse only | adapter-dependent | medium |
| POST | `/setup-wizard/runs/<id>/interfaces/candidates` | Interface candidate filtering | no/router-state input | none | low |
| POST | `/setup-wizard/runs/<id>/generate-hotspot-script` | Hotspot script preview | DB script preview only | none | medium |
| POST | `/setup-wizard/runs/<id>/verify-hotspot` | Hotspot verification | read/probe/parse only | adapter-dependent | medium |
| POST | `/setup-wizard/runs/<id>/generate-broadband-script` | Broadband script preview | DB script preview only | none | medium |
| POST | `/setup-wizard/runs/<id>/verify-broadband` | Broadband verification | read/probe/parse only | adapter-dependent | medium |
| GET | `/setup-wizard/runs/<id>/summary` | Run summary | no | none | low |
| POST | `/setup-wizard/runs/<id>/dry-run/<step>` | MikroTik dry-run | DB operation queue only | none | medium |
| POST | `/setup-wizard/runs/<id>/apply/<step>` | Lab MikroTik apply | router mutation if adapter configured | live + lab flags | high |
| POST | `/setup-wizard/runs/<id>/rollback/<step>` | Lab MikroTik rollback | router mutation if adapter configured | live + lab flags | high |
| GET | `/setup-wizard/runs/<id>/operations` | Operation list | no | none | low |
| POST | `/setup-wizard/runs/<id>/inventory` | Store pasted inventory | DB only | none | medium |
| GET | `/setup-wizard/runs/<id>/inventory/latest` | Latest inventory | no | none | low |
| POST | `/setup-wizard/runs/<id>/orchestrate/hotspot` | Hotspot orchestration plan | DB/dry-run only | none | medium |
| POST | `/setup-wizard/runs/<id>/orchestrate/broadband` | Broadband orchestration plan | DB/dry-run only | none | medium |
| GET | `/setup-wizard/added-services/catalog` | Added services catalog | no | none | low |
| POST | `/setup-wizard/runs/<id>/added-services/plan` | Added service plan | DB/preview only | none | medium |
| POST | `/setup-wizard/runs/<id>/added-services/dry-run` | Added service dry-run | DB operation queue only | none | medium |
| POST | `/setup-wizard/runs/<id>/added-services/apply` | Added service apply via operation engine | router mutation if enabled | live + lab flags | high |
| POST | `/setup-wizard/runs/<id>/added-services/verify` | Verification guidance | no | none | low |
| GET | `/setup-wizard/runs/<id>/support-bundle` | Sanitized support bundle | no | none | low |
| GET | `/setup-wizard/runs/<id>/health` | Run health | no | none | low |
| GET | `/setup-wizard/runs/<id>/pilot-drill` | Pilot drill checklist | no | none | low |
| GET | `/setup-wizard/runs/<id>/recovery` | Recovery analysis | no | none | low |
| POST | `/setup-wizard/runs/<id>/recovery/resume` | Resume guidance | DB/read-only | none | medium |
| POST | `/setup-wizard/runs/<id>/recovery/retry-verification` | Retry verification | read/probe/parse only | adapter-dependent | medium |
| POST | `/setup-wizard/runs/<id>/recovery/regenerate-script` | Regenerate with existing allocation | DB/script preview only | none | medium |
| POST | `/setup-wizard/runs/<id>/recovery/abandon-step` | Mark abandoned | DB only | none | medium |
| POST | `/setup-wizard/runs/<id>/recovery/retire-router` | Retire router lifecycle | DB lifecycle only | none | medium |

## Test Summary

Last audit run results for Prompt 8:

- `python -m compileall app`: passed.
- `node --check app/static/js/setup_wizard_v2.js`: passed.
- `node --check app/static/js/setup_wizard_fleet.js`: passed.
- Broad setup-wizard-related explicit suite:
  - Command selected files matching `test_setup_wizard`, `test_router_`, `test_server_wireguard`, and `test_wireguard_peer_health`.
  - 202 passed.
  - 22 failed.
  - 13,504 warnings.
  - Runtime: 108.53s.
  - Failure pattern: `sqlite3.OperationalError: no such table: setup_wizard_runs` after later router provisioning/lifecycle files run in the same process.
  - Interpretation: core focused files pass individually; the broad same-process suite has a test isolation/order issue that must be fixed before claiming full regression health.
- Full `python -m pytest -q`:
  - Pending/recorded in execution log for this prompt.

This report does not claim full project regression readiness.

## Readiness Scores

| Category | Score | Explanation |
| --- | ---: | --- |
| Architecture readiness | 86 | Strong layered architecture now exists: planners, registry, lifecycle, verification, recovery, fleet, support bundle, V2 UI. Remaining issue is test isolation and production adapter maturity. |
| Safety readiness | 88 | Defaults are blocked, validators are centralized, rollback is scoped, secrets are masked. Remaining risk is that high-risk endpoints exist and must stay behind operational controls. |
| Lab readiness | 82 | CHR/VPS lab flow is viable with flags, dry-run, backup, verify, rollback. Needs real lab evidence and operator checklist execution. |
| UX readiness | 78 | V2 is guided and beginner-first with hidden advanced details. Needs browser visual QA, live Arabic copy pass, and confidence journey state binding. |
| Multi-router readiness | 80 | Registry allocation, lifecycle, fleet dashboard, 50-router simulations exist. Needs long-run soak and concurrency stress under real DB settings. |
| Customer production readiness | 42 | Script-preview assisted onboarding may be usable with internal operators, but one-click production automation is explicitly not ready. Requires security, permission, disaster recovery, support, and real pilot evidence. |

## Pilot Checklist

### Before First Lab CHR

- Confirm lab CHR is not a customer router.
- Take CHR `/export hide-sensitive` and a binary backup.
- Confirm out-of-band access to CHR console.
- Confirm VPS backup/snapshot.
- Confirm WireGuard config backup path.
- Confirm `HOBERADIUS_SETUP_WIZARD_LAB_MODE=true` only in the lab.
- Confirm server apply flags only if testing server peer apply:
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=true`
- Keep MikroTik live apply off unless explicitly testing the lab operation engine:
  - `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY=false`
- Run inventory collection first.
- Run dry-run.
- Review operation queue.
- Apply one isolated server peer only if testing the server WG adapter.
- Verify peer exists.
- Wait for handshake and check health.
- Perform rollback drill.
- Verify peer absence after rollback.
- Generate support bundle and inspect for secret masking.

### Before First Customer

- Complete production security review.
- Complete permissions/admin guard review.
- Complete rate limiting and CSRF review for all Setup Wizard endpoints.
- Complete backup/restore SOP.
- Complete disaster recovery drill.
- Complete operator training.
- Complete customer support playbook.
- Complete legal/support disclaimer for generated MikroTik scripts.
- Fix broad setup-wizard test isolation/order failure.
- Run browser visual QA for V2 and fleet dashboard.
- Run a real CHR lab pilot end-to-end at least twice.
- Run a guarded rollback test in lab.
- Prove support bundle contains no plaintext secrets.

## Next 10 Blockers

1. Fix broad same-process setup-wizard test isolation that causes `setup_wizard_runs` missing table failures.
2. Run real CHR lab pilot with internet preview, inventory, dry-run, server peer apply, verify, and rollback.
3. Produce lab evidence logs/screenshots for success and failure cases.
4. Add production permission/role checks specific to Setup Wizard apply and recovery endpoints.
5. Add rate limiting or operator throttling for apply/rollback/verification endpoints.
6. Perform browser visual QA for V2 mobile and desktop.
7. Add operational monitoring around apply/rollback attempts.
8. Add disaster recovery SOP and operator training checklist.
9. Validate migration behavior on a copy of production-like SQLite/DB state.
10. Decide whether customer-facing mode remains script-preview only or gets a separate production automation certification path.

## Final Verdict

The Setup Wizard is ready for controlled internal pilot testing in a CHR/VPS lab.

The Setup Wizard is not ready for unsupervised customer production automation.

The safest external-facing posture is:

- Allow guided script previews.
- Allow pasted-output verification.
- Allow support bundle generation.
- Keep all apply paths off.
- Use internal operators for every customer setup until the lab checklist, test isolation, security review, and disaster recovery evidence are complete.

Do not market this as zero-touch production automation yet. Market it, at most, as an assisted setup wizard with internal operator supervision until the blockers are closed.
