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
