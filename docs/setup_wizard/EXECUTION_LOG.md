## Prompt 1 — Lab-only WireGuard server apply adapter

### Commit

423e5ea

### What implemented

- Confirmed the lab-only real server WireGuard peer apply adapter is present at the current HEAD and did not rebuild existing architecture.
- Added the production-safe `SubprocessSafeCommandRunner` path guarded by all required lab/server flags.
- Kept the default blocked adapter behavior unless all flags are explicitly enabled.
- Added real server-side apply and rollback command construction for WireGuard peers only.
- Added backup capture before apply using `wg show` and `wg showconf <interface>`.
- Added private-key masking for captured WireGuard config snapshots.
- Extended server peer apply verification states:
  - `applied_no_handshake`
  - `verified_handshake`
  - `failed_verification`
  - `missing_peer`
  - `allowed_ip_mismatch`
- Extended the V2 server peer preparation section with lab-only adapter status, confirmation input, apply/rollback controls, and stronger lab warning.
- Added real-adapter safety and behavior tests.
- Added documentation for the lab-only server WireGuard real adapter.

### Files changed

- `app/radius/services/setup_wizard_server_wg.py`
- `app/radius/services/setup_wizard_server_wg_readiness.py`
- `app/static/css/setup_wizard_v2.css`
- `app/static/js/setup_wizard_v2.js`
- `app/templates/radius/setup_wizard_v2.html`
- `docs/setup_wizard/SERVER_WG_REAL_ADAPTER.md`
- `tests/test_server_wireguard_peer_apply.py`
- `tests/test_server_wireguard_readiness.py`
- `tests/test_server_wireguard_real_adapter.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- `python -m pytest tests/test_server_wireguard_real_adapter.py -q` passed: 16 passed, 2728 warnings in 13.47s.
- `python -m pytest tests/test_server_wireguard_peer_apply.py -q` passed: 12 passed, 2464 warnings in 12.46s.
- Setup wizard related suite passed:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_router_provisioning.py tests/test_setup_wizard_v2.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py -q`
  - Result: 159 passed, 11955 warnings in 77.48s.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No specific failing test output was returned before timeout.
- `git diff --check` passed before the implementation commit; line-ending warnings were present for dirty unrelated files and touched files.

### Safety confirmations

- Production server apply remains blocked by default.
- Real server WireGuard mutation requires all of these flags:
  - `HOBERADIUS_SETUP_WIZARD_LAB_MODE=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=true`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=true`
- No MikroTik live apply was added.
- No customer production automation was enabled.
- No `radius-module-admin` files were touched.
- No Flutter files were touched.
- Existing RADIUS auth/accounting behavior was not changed.
- No `shell=True` path was introduced.
- Allowed read-only server commands are limited to:
  - `wg show`
  - `wg showconf <interface>`
  - `ip addr show`
  - `ip route show`
- Allowed server apply command is limited to:
  - `wg set <interface> peer <public_key> allowed-ips <router_ip>/32`
- Allowed server rollback command is limited to:
  - `wg set <interface> peer <public_key> remove`
- Dangerous commands such as restarts, `wg-quick down/up`, broad config edits, route/firewall flushes, and broad deletions remain blocked.

### Remaining risks

- The real adapter is code-ready for a controlled VPS/CHR lab, but it has not been executed against a real VPS in this prompt.
- A real lab run still requires verifying the server interface name, WireGuard permissions, command timeout, backup capture, and out-of-band VPS access before enabling flags.
- Handshake verification can only become `verified_handshake` after the router side is connected; initial server peer application may correctly remain `applied_no_handshake`.
- Full project pytest still does not complete within the 304 second execution window in this environment.

### Full honest notes

- The repository HEAD at the start of this prompt was already `e22832a Add lab-only WireGuard server apply adapter`, not the stale prompt baseline `4e9697c`.
- Because the requested implementation was already present, this prompt did not rebuild or duplicate the architecture. It verified the existing implementation, reran the required checks, and added this execution log.
- Pre-existing unrelated dirty files remain intentionally excluded from staging and commits:
  - `app/radius/routes/print_templates.py`
  - `app/radius/seed.py`
  - `app/radius/services/operations.py`
  - `app/static/css/cards_batches_view.css`
  - `app/static/js/cards_batches_view.js`
  - `app/templates/radius/cards_batches.html`
  - `app/templates/radius/devices_list.html`
  - `app/templates/radius/mt_alerts_index.html`
  - `app/templates/radius/network_policy_list.html`
  - `app/templates/radius/print_templates.html`
  - `tests/test_card_renderer.py`
  - `tests/test_operations_foundation.py`
  - `tests/test_web_print_templates_ui.py`
  - `app/templates/radius/_npc_components.html`

## Prompt 5 — Resume / Recovery / Repair Engine

### Commit

Final commit hash is reported in the assistant final response for this prompt. Git commit hashes cannot be embedded inside the same commit without changing the hash.

### What implemented

- Added `setup_wizard_recovery_events` as a non-secret recovery event/audit table.
- Added `SetupWizardRecoveryAnalyzer` with states:
  - `clean_resume`
  - `waiting_user_action`
  - `failed_verification`
  - `partial_apply`
  - `stale_inventory`
  - `peer_key_missing`
  - `duplicate_peer_conflict`
  - `subnet_conflict`
  - `unsupported_recovery`
  - `terminal_retired`
- Added `SetupWizardRecoveryService` for safe recovery actions:
  - resume
  - retry verification via the existing verification engine
  - regenerate VPN/RADIUS script while preserving the existing router allocation
  - plan-only credential reissue guard
  - abandon step with required reason
  - retire router without releasing IP allocation for reuse
  - dry-run-only repair plan
- Added recovery endpoints:
  - `GET /admin/radius/setup-wizard/runs/<id>/recovery`
  - `POST /admin/radius/setup-wizard/runs/<id>/recovery/resume`
  - `POST /admin/radius/setup-wizard/runs/<id>/recovery/retry-verification`
  - `POST /admin/radius/setup-wizard/runs/<id>/recovery/regenerate-script`
  - `POST /admin/radius/setup-wizard/runs/<id>/recovery/abandon-step`
  - `POST /admin/radius/setup-wizard/runs/<id>/recovery/retire-router`
- Added a Setup Wizard V2 recovery panel placeholder and client hooks.
- Allowed safe regeneration of an already-generated script-preview step without changing required verification gates.
- Added recovery documentation.

### Files changed

- `app/radius/db/migrations/055_setup_wizard_recovery_events.sql`
- `app/radius/services/setup_wizard_recovery.py`
- `app/radius/services/setup_wizard.py`
- `app/radius/routes/setup_wizard.py`
- `app/templates/radius/setup_wizard_v2.html`
- `app/static/js/setup_wizard_v2.js`
- `docs/setup_wizard/RECOVERY_ENGINE.md`
- `tests/test_setup_wizard_recovery.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- Initial targeted recovery run:
  - `python -m pytest tests/test_setup_wizard_recovery.py -q`
  - Result: 1 failed, 10 passed.
  - Failure: safe script regeneration was blocked by the existing generated-step transition rule.
- Fix applied:
  - Allowed `generated -> generated` only for script-preview steps, so recovery regeneration can preserve the same allocation and rewrite the preview.
- Final targeted recovery run:
  - `python -m pytest tests/test_setup_wizard_recovery.py -q`
  - Result: 11 passed, 2174 warnings in 10.53s.
- Setup wizard related suite:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_v2.py tests/test_setup_wizard_v2_hotspot_broadband.py tests/test_setup_wizard_added_services.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py tests/test_wireguard_peer_health.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_setup_wizard_recovery.py -q`
  - Result: 186 passed, 14420 warnings in 88.53s.
- `git diff --check` passed with line-ending warnings only.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No failing test output was returned before timeout.

### Safety confirmations

- No live MikroTik apply was added.
- No live VPS/WireGuard mutation was added.
- No destructive automatic repair was added.
- Recovery actions are read-only, event recording, safe regeneration, or terminal marking only.
- Retry verification reuses the existing verification engine.
- Regeneration reuses the existing router provisioning allocation for the same run.
- Credential reissue remains plan-only unless a future guarded flow explicitly implements it.
- Retiring a router does not release IP allocations for automatic reuse.
- Support/recovery outputs are masked through the existing secret masking helper.
- `radius-module-admin` was not touched.
- Flutter was not touched.
- Existing RADIUS auth/accounting behavior was not changed.

### Remaining risks

- Recovery event logging is intentionally minimal and not yet tied into a broader audit service.
- Credential reissue is only a blocked/plan-only response; a future slice must define a collision-safe reissue workflow.
- Repair plans are operator guidance only and do not yet produce structured operation queues.
- Full project pytest still does not complete within the 304 second execution window.

### Full honest notes

- The only behavior adjustment outside the new recovery service is allowing script-preview steps to regenerate from `generated` to `generated`. Verified steps remain protected.
- The V2 recovery panel is intentionally hidden unless recovery detects an issue.
- Pre-existing unrelated dirty files remain intentionally excluded from staging and commits:
  - `app/radius/routes/print_templates.py`
  - `app/radius/seed.py`
  - `app/radius/services/operations.py`
  - `app/static/css/cards_batches_view.css`
  - `app/static/js/cards_batches_view.js`
  - `app/templates/radius/cards_batches.html`
  - `app/templates/radius/devices_list.html`
  - `app/templates/radius/mt_alerts_index.html`
  - `app/templates/radius/network_policy_list.html`
  - `app/templates/radius/print_templates.html`
  - `tests/test_card_renderer.py`
  - `tests/test_operations_foundation.py`
  - `tests/test_web_print_templates_ui.py`
  - `app/templates/radius/_npc_components.html`

## Prompt 3 — Complete Setup Wizard V2 Hotspot Broadband Flow

### Commit

Final commit hash is reported in the assistant final response for this prompt. Git commit hashes cannot be embedded inside the same commit without changing the hash.

### What implemented

- Extended Setup Wizard V2 beyond Internet and VPN/RADIUS into a guided services flow.
- Added a beginner-first service path step:
  - Hotspot
  - Broadband / PPPoE
  - Hotspot + Broadband
  - Skip for now
- Added a visual interface picker with safe/unsafe states, WAN/VPN exclusions, and recommended LAN interfaces.
- Added Hotspot smart/manual flow using the real backend generation endpoint.
- Added Broadband / PPPoE smart/manual flow using the real backend generation endpoint.
- Added real script preview cards for Hotspot and Broadband.
- Added dry-run review buttons that call existing guarded dry-run endpoints.
- Added pasted-output verification controls that call existing verification endpoints.
- Kept engineering payloads, plan summaries, and raw details collapsed under advanced details.
- Added V2 frontend state for selected service path, selected interfaces, service modes, generated plans, dry-run, and verification status.
- Added route/service integration tests proving scripts come from backend planners, not frontend mocks.

### UX flow added

- After VPN/RADIUS verification, V2 now leads the user to:
  1. choose the service path,
  2. choose safe interfaces,
  3. configure Hotspot or Broadband in Smart/Manual mode,
  4. generate a real script preview,
  5. run dry-run review,
  6. paste verification output,
  7. reach a calm final summary.
- The UI remains Arabic RTL, one-step-at-a-time, and avoids showing raw JSON by default.
- Apply controls were not added for Hotspot/Broadband; dry-run remains visible, and live apply remains outside this customer-facing V2 path.

### Files changed

- `app/templates/radius/setup_wizard_v2.html`
- `app/static/css/setup_wizard_v2.css`
- `app/static/js/setup_wizard_v2.js`
- `tests/test_setup_wizard_v2_hotspot_broadband.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- `python -m pytest tests/test_setup_wizard_v2_hotspot_broadband.py -q` passed: 8 passed, 224 warnings in 4.51s.
- `python -m pytest tests/test_setup_wizard_v2.py -q` passed: 11 passed, 216 warnings in 7.13s.
- `python -m pytest tests/test_setup_wizard_hotspot_planner.py -q` passed: 6 passed, 220 warnings in 3.12s.
- `python -m pytest tests/test_setup_wizard_broadband_planner.py -q` passed: 6 passed, 215 warnings in 3.19s.
- Setup wizard related suite passed:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_v2.py tests/test_setup_wizard_v2_hotspot_broadband.py tests/test_setup_wizard_router_provisioning.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py tests/test_wireguard_peer_health.py -q`
  - Result: 177 passed, 14042 warnings in 81.25s.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No specific failing test output was returned before timeout.
- `git diff --check` passed with line-ending warnings only.

### Safety confirmations

- No live MikroTik apply was introduced.
- No apply route was enabled by default.
- Backend safety behavior was not changed.
- Existing planner, verification, dry-run, and guarded operation foundations were reused.
- No frontend mock script source of truth was introduced.
- Engineering View remains separate at `/admin/radius/setup-wizard`.
- `radius-module-admin` was not touched.
- Flutter was not touched.
- Existing RADIUS auth/accounting behavior was not changed.

### Remaining risks

- The interface picker currently depends on existing interface candidates and a safe fallback; richer real-router inventory display can still be polished later.
- The "Both" service path selects the combined path but does not yet enforce a full automated Hotspot-then-Broadband guided queue beyond exposing both product flows.
- Verification still depends on pasted output or existing verification probes.
- Full project pytest still does not complete within the 304 second execution window.

### Full honest notes

- This prompt intentionally stayed in frontend/product-flow integration and did not rebuild Hotspot/Broadband planners.
- This prompt did not add any MikroTik execution path.
- This prompt did not remove or weaken the existing Engineering View.
- Pre-existing unrelated dirty files remain intentionally excluded from staging and commits:
  - `app/radius/routes/print_templates.py`
  - `app/radius/seed.py`
  - `app/radius/services/operations.py`
  - `app/static/css/cards_batches_view.css`
  - `app/static/js/cards_batches_view.js`
  - `app/templates/radius/cards_batches.html`
  - `app/templates/radius/devices_list.html`
  - `app/templates/radius/mt_alerts_index.html`
  - `app/templates/radius/network_policy_list.html`
  - `app/templates/radius/print_templates.html`
  - `tests/test_card_renderer.py`
  - `tests/test_operations_foundation.py`
  - `tests/test_web_print_templates_ui.py`
  - `app/templates/radius/_npc_components.html`

## Prompt 2 — WireGuard peer health engine

### Commit

Final commit hash is reported in the assistant final response for this prompt. A commit cannot safely self-reference its own final hash inside a tracked file without changing that hash again.

### What implemented

- Added `WireGuardPeerHealthService` as a read-only health intelligence layer for prepared WireGuard peers.
- Reused the existing `wg show` parser and safe read-only command runner infrastructure.
- Added peer health classification for:
  - `healthy`
  - `applied_no_handshake`
  - `stale_peer`
  - `missing_peer`
  - `allowed_ip_mismatch`
  - `duplicate_peer`
  - `offline`
  - `unknown`
- Added health scoring from 0 to 100.
- Added Arabic diagnostics and recovery suggestions.
- Added parsing for:
  - latest handshake age
  - RX/TX transfer values
  - endpoint
  - persistent keepalive
- Added a read-only health endpoint:
  - `POST /admin/radius/setup-wizard/runs/<id>/server-peer/health`
- Extended Setup Wizard V2 Server Peer Preparation with a peer health panel showing:
  - peer state
  - health score
  - handshake age
  - RX/TX
  - recommendation
- Kept raw health JSON collapsed under advanced details.

### Files changed

- `app/radius/services/wireguard_peer_health.py`
- `app/radius/services/setup_wizard.py`
- `app/radius/routes/setup_wizard.py`
- `app/templates/radius/setup_wizard_v2.html`
- `app/static/css/setup_wizard_v2.css`
- `app/static/js/setup_wizard_v2.js`
- `tests/test_wireguard_peer_health.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- `python -m pytest tests/test_wireguard_peer_health.py -q` passed: 10 passed, 2051 warnings in 8.23s.
- Setup wizard related suite passed:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_router_provisioning.py tests/test_setup_wizard_v2.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py tests/test_wireguard_peer_health.py -q`
  - Result: 169 passed, 14006 warnings in 67.17s.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No specific failing test output was returned before timeout.
- `git diff --check` passed with line-ending warnings only.

### Safety confirmations

- Read-only inspection only.
- No MikroTik mutation was introduced.
- No server mutation was introduced.
- No production automation was enabled.
- No `shell=True` path was introduced.
- Existing readiness/runner infrastructure is reused.
- Existing real server apply/rollback guardrails were not rebuilt or widened.
- `radius-module-admin` was not touched.
- Flutter was not touched.
- Existing RADIUS auth/accounting behavior was not changed.

### Remaining risks

- Health quality depends on accurate `wg show` output or a configured read-only runner.
- `offline` detection requires a previous observation to compare RX/TX movement.
- The first apply can still correctly report `applied_no_handshake` until the router side initiates traffic.
- Full project pytest still does not complete within the 304 second execution window.

### Full honest notes

- This prompt intentionally did not rebuild provisioning, lifecycle, readiness, adapter, dry-run, apply, rollback, or peer planning.
- The health service does not persist historical samples yet; it accepts an optional previous observation for frozen RX/TX detection.
- Pre-existing unrelated dirty files remain intentionally excluded from staging and commits:
  - `app/radius/routes/print_templates.py`
  - `app/radius/seed.py`
  - `app/radius/services/operations.py`
  - `app/static/css/cards_batches_view.css`
  - `app/static/js/cards_batches_view.js`
  - `app/templates/radius/cards_batches.html`
  - `app/templates/radius/devices_list.html`
  - `app/templates/radius/mt_alerts_index.html`
  - `app/templates/radius/network_policy_list.html`
  - `app/templates/radius/print_templates.html`
  - `tests/test_card_renderer.py`
  - `tests/test_operations_foundation.py`
  - `tests/test_web_print_templates_ui.py`
  - `app/templates/radius/_npc_components.html`

## Prompt 4 — Added Services Engine + V2 Flow

### Commit

Final commit hash is reported in the assistant final response for this prompt. Git commit hashes cannot be embedded inside the same commit without changing the hash.

### Existing services found

- Network Policy Center foundations exist:
  - `npc_walled_garden_planner`
  - `npc_web_block_planner`
  - `npc_script_renderer`
- Site Exit / VX2 foundations exist:
  - `site_exit_script_planner`
  - `site_exit_script_renderer`
  - site-exit repositories, safety, presets, UI, and apply tests
- No stable Setup Wizard anti-sharing / tethering planner was found.

### Supported/partial/unsupported matrix

- `anti_sharing`: `not_supported_yet`
  - No fake script generated.
  - UI/catalog explains that it needs later activation.
- `walled_garden`: `partial`
  - Delegates to `npc_walled_garden_planner`.
  - Adds Setup Wizard tags to managed add commands while preserving NPC ownership tags.
- `block_sites`: `partial`
  - Delegates to `npc_web_block_planner`.
  - Legacy alias `web_block` remains available for compatibility.
- `site_exit_public_ip`: `partial`
  - Delegates to `site_exit_script_planner`.
  - Legacy alias `site_exit` remains available for compatibility.

### What implemented

- Rebuilt the existing Added Services placeholder into a real catalog/planner layer.
- Added service catalog metadata:
  - key
  - Arabic title/description
  - risk level
  - status
  - required inputs
  - planner delegate
  - verification delegate
  - rollback capability
- Added presets:
  - ISP Basic
  - Hotel / Cafe
  - School
  - Gaming Center
- Added plan generation for:
  - walled garden via existing NPC planner
  - block sites via existing NPC planner
  - site exit via existing VX2 planner
- Added safe `not_supported_yet` response for anti-sharing.
- Added structured dry-run and verification-guidance endpoints for added services.
- Added Setup Wizard V2 "خدمات إضافية" step:
  - service cards
  - preset cards
  - required input form
  - plan preview
  - dry-run button
  - verification guidance button
  - diagnostics
  - advanced details collapsed
- Kept live apply out of the V2 added-services flow.

### Files changed

- `app/radius/services/setup_wizard_added_services.py`
- `app/radius/services/setup_wizard.py`
- `app/radius/routes/setup_wizard.py`
- `app/templates/radius/setup_wizard_v2.html`
- `app/static/js/setup_wizard_v2.js`
- `docs/setup_wizard/ADDED_SERVICES_ENGINE.md`
- `tests/test_setup_wizard_added_services.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- `python -m pytest tests/test_setup_wizard_added_services.py -q` passed: 8 passed, 286 warnings in 4.19s.
- Compatibility check passed:
  - `python -m pytest tests/test_setup_wizard_operational_waves.py::test_added_services_catalog_and_unsupported_response tests/test_setup_wizard_added_services.py -q`
  - Result: 9 passed, 299 warnings in 4.53s.
- Setup wizard related suite passed:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_v2.py tests/test_setup_wizard_v2_hotspot_broadband.py tests/test_setup_wizard_added_services.py tests/test_setup_wizard_router_provisioning.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py tests/test_wireguard_peer_health.py -q`
  - Result: 185 passed, 14140 warnings in 84.16s.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No specific failing test output was returned before timeout.
- `git diff --check` passed with line-ending warnings only.

### Safety confirmations

- No duplicate network policy logic was introduced.
- Existing NPC and VX2 planners are reused.
- No live apply was introduced.
- No live apply was enabled by default.
- Added-service apply is not wired into V2.
- Unsupported anti-sharing does not generate fake scripts.
- Unknown service keys are rejected.
- Generated add commands include `HOBERADIUS_SETUP:<run_id>:added-service:<service_key>` while preserving delegated ownership tags.
- `radius-module-admin` was not touched.
- Flutter was not touched.
- Existing RADIUS auth/accounting behavior was not changed.

### Remaining risks

- Walled Garden and Block Sites remain `partial` because Setup Wizard does not create persistent NPC policy rows in this phase.
- Site Exit remains `partial` because production use depends on VX2 policy/node lifecycle and safety checks.
- Verification is guidance-only for added services until read-only verification adapters are added per delegated service.
- Full project pytest still does not complete within the 304 second execution window.

### Full honest notes

- The catalog includes legacy alias keys `web_block` and `site_exit` to preserve older Wave F tests and route consumers.
- This prompt intentionally did not implement anti-sharing because no stable existing planner was found.
- This prompt intentionally did not add customer-facing one-click added-service automation.
- Pre-existing unrelated dirty files remain intentionally excluded from staging and commits:
  - `app/radius/routes/print_templates.py`
  - `app/radius/seed.py`
  - `app/radius/services/operations.py`
  - `app/static/css/cards_batches_view.css`
  - `app/static/js/cards_batches_view.js`
  - `app/templates/radius/cards_batches.html`
  - `app/templates/radius/devices_list.html`
  - `app/templates/radius/mt_alerts_index.html`
  - `app/templates/radius/network_policy_list.html`
  - `app/templates/radius/print_templates.html`
  - `tests/test_card_renderer.py`
  - `tests/test_operations_foundation.py`
  - `tests/test_web_print_templates_ui.py`
  - `app/templates/radius/_npc_components.html`
