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

## Sidebar Navigation Links

### Setup wizard routes added

- `/admin/radius/setup-wizard-v2` as `معالج الإعداد`
- `/admin/radius/setup-wizard/fleet` as `أسطول الراوترات`
- `/admin/radius/setup-wizard` as `عرض الإعداد الهندسي`

### Operations / fleet routes added

- Setup Wizard fleet is exposed through the `الإعداد والتشغيل` sidebar group.
- Business operations pages are exposed through the `نظام الأعمال` sidebar group, including `/operations` and `/operations/speed-control`.

### Files changed

- `app/templates/admin/_sidebar.html`
- `tests/test_setup_wizard_sidebar_links.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Active-state behavior

- Setup Wizard links use exact path matching so `/setup-wizard` does not activate `/setup-wizard/fleet`.
- The sidebar opens the containing section only for the active page.

### Tests run

- `python -m compileall app` passed.
- `python -m pytest tests/test_setup_wizard_sidebar_links.py -q` passed: `3 passed, 807 warnings in 12.31s`.
- `python -m pytest tests/test_business_os_sidebar_navigation.py -q` passed: `3 passed, 808 warnings in 14.16s`.
- `python -m pytest tests/test_admin_bridge_sidebar_navigation.py -q` passed: `3 passed, 816 warnings in 12.65s`.
- Relevant Business OS web suite passed: `55 passed, 14527 warnings in 46.67s`.
- `git diff --check` passed.

### Safety confirmations

- No action/API routes were exposed in the sidebar.
- No live apply or production automation changed.
- No RADIUS, MikroTik, FreeRADIUS, or CoA behavior changed.

## Blocker Fix — Setup Wizard DB Isolation / Migration Order

### Commit

Final commit hash is reported in the assistant final response for this blocker fix. Git commit hashes cannot be embedded inside the same commit without changing the hash again.

### What implemented

- Reproduced the broad same-process failure with the repo-matching Setup Wizard/router/WireGuard test list:
  - `tests/test_router_snapshots_s7.py` ran before `tests/test_server_wireguard_peer_apply.py`.
  - Failure: `sqlite3.OperationalError: no such table: setup_wizard_runs`.
  - First failing test: `test_server_peer_apply_blocked_by_default_flags`.
- Root cause:
  - `tests/test_router_snapshots_s7.py` deleted `app.*` modules from `sys.modules` to force DB isolation.
  - Other already-collected Setup Wizard tests held imported service functions and DB helpers from earlier module instances.
  - That split migration/app initialization and service calls across different cached DB connection modules in the same pytest process.
  - The SQLite connection cache also did not honor a changed `HOBERADIUS_DB_PATH` after `_db_path` had already been resolved unless a test explicitly called `reset_for_tests`.
- Fix:
  - `app.radius.db.connection.db_path()` now honors a changed `HOBERADIUS_DB_PATH`, closes the current thread connection, and creates the new parent directory before opening the next connection.
  - `tests/test_router_snapshots_s7.py` now uses `reset_for_tests(db_file)` instead of deleting `app.*` modules.
  - Added `tests/test_setup_wizard_db_isolation.py` to prove the standard app/migration path creates the full Setup Wizard schema and that DB path switches remain deterministic.
- Updated the production readiness report to mark the broad DB isolation/order blocker as fixed.

### Files changed

- `app/radius/db/connection.py`
- `tests/test_router_snapshots_s7.py`
- `tests/test_setup_wizard_db_isolation.py`
- `docs/setup_wizard/PRODUCTION_READINESS_REPORT.md`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- Before fix, repo-matching broad same-process suite with `-x` failed:
  - Command included `test_setup_wizard*.py`, `test_router_*.py`, `test_server_wireguard*.py`, and `test_wireguard_peer_health.py`.
  - Result: 38 passed, 1 failed, 6,250 warnings in 102.45s.
  - Failure: `sqlite3.OperationalError: no such table: setup_wizard_runs` in `SetupWizardService.create_run`.
- `python -m compileall app` passed.
- `python -m pytest tests/test_setup_wizard_db_isolation.py -q` passed: 3 passed, 570 warnings in 5.43s.
- `python -m pytest tests/test_router_lifecycle.py -q` passed: 5 passed, 963 warnings in 18.88s.
- `python -m pytest tests/test_router_provisioning_orchestrator.py -q` passed: 12 passed, 2,592 warnings in 44.61s.
- `python -m pytest tests/test_router_fleet_dashboard.py -q` passed: 9 passed, 1,841 warnings in 35.37s.
- `python -m pytest tests/test_setup_wizard_recovery.py -q` passed: 11 passed, 2,174 warnings in 41.11s.
- `python -m pytest tests/test_setup_wizard_foundation.py -q` passed: 8 passed, 236 warnings in 4.77s.
- Repo-matching broad same-process suite passed:
  - Command included every matching file from `test_setup_wizard*.py`, `test_router_*.py`, `test_server_wireguard*.py`, and `test_wireguard_peer_health.py`.
  - Result: 224 passed, 18,675 warnings in 258.28s.
- Prompt-listed broad same-process suite passed:
  - Result: 195 passed, 16,261 warnings in 139.36s.
- `git diff --check` passed with line-ending warnings only.
- `python -m pytest -q` was attempted and timed out after 304.0s with no visible failure output before timeout.

### Safety confirmations

- No live apply was enabled.
- No production automation behavior was changed.
- No MikroTik behavior was changed.
- No server WireGuard behavior was changed.
- No RADIUS auth/accounting behavior was changed.
- No radius-module-admin files were touched.
- No Flutter files were touched.
- No failing tests were skipped, xfailed, reordered, or weakened.
- No production tables are silently created from request handlers.

### Remaining risks

- Full `python -m pytest -q` still does not complete inside the 304 second execution window.
- The suite still emits many existing deprecation warnings around `datetime.utcnow()`.
- Several unrelated dirty files remain in the worktree and must remain excluded from staging.

### Full honest notes

- The exact older prompt-listed broad suite passed even before the fix in this checkout because it did not include `tests/test_router_snapshots_s7.py`.
- The actual repo-matching broad suite did reproduce the missing table failure before the fix and passed after the fix.
- This fix is order-independent because tests no longer delete `app.*` modules to force isolation, and the DB connection layer now follows the current `HOBERADIUS_DB_PATH` when tests switch temporary databases in the same process.

## CHR/VPS Lab Pilot Preparation

### Commit

Final commit hash is reported in the assistant final response for this prompt. Git commit hashes cannot be embedded inside the same commit without changing the hash again.

### What implemented

- Prepared the first controlled CHR/VPS Setup Wizard lab pilot evidence package.
- Created a lab pilot runbook for the internal engineering flow:
  - preview,
  - inventory,
  - dry-run,
  - lab-only server WireGuard peer apply if all flags/readiness checks pass,
  - verify,
  - rollback drill,
  - Hotspot/Broadband preview and dry-run,
  - support bundle masking review.
- Created a blank evidence template with explicit placeholders for:
  - environment,
  - flags,
  - wizard run metadata,
  - allocated VPN IP,
  - script checksums,
  - inventory,
  - readiness,
  - dry-run,
  - backup,
  - apply,
  - masked `wg show`,
  - health,
  - rollback,
  - support bundle masking,
  - screenshots,
  - final verdict.
- No actual lab execution was performed because this environment does not expose a controlled CHR/VPS, lab credentials, server WireGuard interface, or operator confirmation path.
- No fake evidence file was created.

### Files changed

- `docs/setup_wizard/CHR_VPS_LAB_PILOT_RUNBOOK.md`
- `docs/setup_wizard/CHR_VPS_LAB_EVIDENCE_TEMPLATE.md`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `python -m pytest tests/test_setup_wizard_db_isolation.py -q` passed: 3 passed, 570 warnings in 4.85s.
- Broad prompt-listed Setup Wizard suite with the DB isolation test included passed:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_v2.py tests/test_setup_wizard_v2_hotspot_broadband.py tests/test_setup_wizard_added_services.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py tests/test_wireguard_peer_health.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_setup_wizard_recovery.py tests/test_router_fleet_dashboard.py tests/test_setup_wizard_db_isolation.py -q`
  - Result: 198 passed, 16,831 warnings in 116.34s.
- `git diff --check` passed with line-ending warnings only.
- `git status --short` showed only pre-existing unrelated dirty files; the new docs are ignored by `.gitignore` and were intentionally staged explicitly with force.
- `python -m pytest -q` was attempted and timed out after 304.0s with no visible failure output before timeout.

### Safety confirmations

- Actual CHR/VPS lab execution was not faked.
- No secrets, private keys, real API passwords, real RADIUS secrets, or full WireGuard private configs were committed.
- No live apply was enabled.
- No production automation behavior was changed.
- No MikroTik behavior was changed.
- No server WireGuard behavior was changed.
- No RADIUS auth/accounting behavior was changed.
- No radius-module-admin files were touched.
- No Flutter files were touched.

### Remaining risks

- First real CHR/VPS evidence is still pending manual lab execution.
- Real server peer apply evidence is pending a dedicated lab VPS with all four lab-only flags enabled by an operator.
- Rollback evidence is pending a real applied peer in the lab.
- Hotspot/Broadband real-router evidence is pending inventory from an isolated CHR.
- Full project pytest still does not complete within the 304 second execution window.

### Full honest notes

- This prompt produced preparation artifacts only.
- The evidence template is intentionally blank and marked pending so nobody mistakes a runbook for executed lab proof.
- MikroTik live apply remains explicitly out of scope for this pilot package unless a separate guarded lab operation drill is approved.

## Prompt 8 — Production Readiness Audit + Final Verdict

### Commit

Created by this commit. Exact hash is reported in the final prompt result because a commit cannot contain its own final hash without changing that hash.

### Audit result

- Audited Setup Wizard flags, routes, migrations, support masking, operation safety, rollback scope, server WireGuard readiness/apply guardrails, V2 UI safety, fleet dashboard, recovery endpoints, added services, and operation tables.
- Confirmed live apply defaults are off:
  - `HOBERADIUS_SETUP_WIZARD_LIVE_APPLY`
  - `HOBERADIUS_SETUP_WIZARD_LAB_MODE`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS`
  - `HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER`
- Confirmed server WireGuard real apply requires all lab/server flags, readiness, dry-run, confirmation, backup capture, duplicate checks, and exact narrow `wg set` commands.
- Confirmed MikroTik apply remains behind live + lab flags and guarded operation validation.
- Confirmed rollback is scoped to generated tags/comments.
- Confirmed support/fleet/recovery paths use masking helpers for secrets.
- Confirmed V2 keeps engineering details in collapsed Advanced areas and preserves Engineering View.
- Created `docs/setup_wizard/PRODUCTION_READINESS_REPORT.md`.

### Readiness scores

- Architecture readiness: 86/100
- Safety readiness: 88/100
- Lab readiness: 82/100
- UX readiness: 78/100
- Multi-router readiness: 80/100
- Customer production readiness: 42/100

### Test exact results

- `python -m compileall app`
  - Passed.
- `node --check app/static/js/setup_wizard_v2.js`
  - Passed.
- `node --check app/static/js/setup_wizard_fleet.js`
  - Passed.
- `python -m pytest <setup wizard related tests> -q`
  - Command selected files matching `test_setup_wizard`, `test_router_`, `test_server_wireguard`, and `test_wireguard_peer_health`.
  - Result: 202 passed, 22 failed, 13,504 warnings in 108.53s.
  - Failure pattern: `sqlite3.OperationalError: no such table: setup_wizard_runs` in router provisioning/lifecycle/fleet tests after other setup wizard tests run in the same process.
  - Honest interpretation: not production-regression-clean; likely suite isolation/order issue, but it remains a blocker.
- `python -m pytest -q`
  - Timed out after 304 seconds.
- `git diff --check`
  - Passed with line-ending warnings only.
- `git status --short`
  - Shows only the pre-existing unrelated dirty files plus this ignored readiness doc before explicit staging.

### Final verdict

- Lab-ready: yes, for controlled internal CHR/VPS pilot only.
- Customer-ready: partially, for supervised script-preview onboarding only.
- Production-ready: no, not for unsupervised customer automation or one-click router provisioning.

### Remaining blockers

1. Fix setup-wizard broad-suite isolation/order failures.
2. Run real CHR lab pilot twice with inventory, dry-run, server peer apply, verify, rollback, and support bundle review.
3. Complete browser visual QA for V2 and fleet.
4. Complete production permission review for apply/recovery endpoints.
5. Add rate limiting/throttling for apply, rollback, and verification endpoints.
6. Complete backup/restore and disaster recovery SOPs.
7. Validate migrations on production-like DB copies.
8. Add operator training and support playbook.
9. Prove support bundles contain no plaintext secrets during real lab use.
10. Decide production policy: script-preview-only customer mode vs certified automation track.

### Safety confirmations

- No production automation was enabled.
- No live apply was enabled by default.
- No MikroTik live mutation was introduced.
- No server mutation was introduced in this prompt.
- `radius-module-admin` was not touched.
- Flutter was not touched.
- Existing RADIUS auth/accounting behavior was not changed.

### Full honest notes

- This prompt intentionally added documentation only.
- No optional tests were added because existing tests already cover the critical flag/default/masking/dangerous-command areas; the actual blocker is broad-suite isolation, not missing narrow coverage.
- The production readiness report is intentionally conservative. The Setup Wizard is a strong internal/lab onboarding system, not a sellable zero-touch production automation system yet.
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

## Prompt 7 — UX Productization + Confidence Journey

### Commit

Created by this commit. Exact hash is reported in the final prompt result because a commit cannot contain its own final hash without changing that hash.

### UX improvements

- Added a calm confidence journey above the stepper:
  - `بدأنا`
  - `تم تجهيز الإنترنت`
  - `تم ربط الراوتر`
  - `الشبكة جاهزة`
  - `الخدمات الإضافية`
  - `اكتمل الإعداد`
- Added beginner-first empty state copy for a fresh wizard session.
- Added success explanation cards for:
  - internet verification success
  - VPN/RADIUS verification success
  - final summary
- Polished primary Arabic copy in V2 so it reads less like a developer console:
  - router provisioning labels
  - server peer preparation labels
  - lab confirmation wording
  - dry-run wording
  - Hotspot/Broadband form labels
  - added-services unsupported state
- Kept engineering details in collapsed advanced sections.
- Preserved the Engineering View link and the existing engineering route.
- Improved mobile responsiveness for:
  - V2 action rows and buttons
  - script/advanced panels
  - readiness/health panels
  - fleet dashboard toolbar/table/drawer.

### Files changed

- `app/templates/radius/setup_wizard_v2.html`
- `app/static/css/setup_wizard_v2.css`
- `app/static/css/setup_wizard_fleet.css`
- `tests/test_setup_wizard_v2_productization.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_v2.js` passed.
- `node --check app/static/js/setup_wizard_fleet.js` passed.
- `python -m pytest tests/test_setup_wizard_v2_productization.py -q`
  - Result: 8 passed, 189 warnings in 48.89s.
- Individual regression checks passed:
  - `python -m pytest tests/test_setup_wizard_foundation.py -q`
    - Result: 8 passed, 236 warnings in 12.84s.
  - `python -m pytest tests/test_setup_wizard_v2.py -q`
    - Result: 11 passed, 217 warnings in 16.18s.
  - `python -m pytest tests/test_setup_wizard_v2_hotspot_broadband.py -q`
    - Result: 8 passed, 225 warnings in 14.10s.
  - `python -m pytest tests/test_setup_wizard_added_services.py -q`
    - Result: 8 passed, 287 warnings in 13.26s.
- Broad setup-wizard-related suite was attempted with explicit files:
  - Result: 158 passed, 66 failed, 7398 warnings in 114.26s.
  - Failure pattern: `sqlite3.OperationalError: no such table: setup_wizard_runs` once later provisioning/lifecycle files ran in the same process. Core files pass individually, so this appears to be a pre-existing suite isolation/order issue rather than a UX productization regression.
- `git diff --check` passed with line-ending warnings only.
- `python -m pytest -q` was attempted and timed out after 304 seconds.

### Safety confirmations

- Backend behavior was not changed.
- No live MikroTik apply was introduced.
- No live server apply was introduced.
- Live apply remains disabled by default.
- Engineering View remains available at `/admin/radius/setup-wizard`.
- Advanced/engineering JSON blocks remain collapsed by default.
- No RADIUS auth/accounting behavior was touched.
- `radius-module-admin` was not touched.
- Flutter was not touched.

### Remaining visual gaps

- The confidence journey is currently static; a later UI-only slice can bind it to the live JS step state.
- Some technical product terms remain intentionally in English where they are protocol/product names: `VPN/RADIUS`, `WireGuard`, `Peer`, `Hotspot`, `Broadband`, `PPPoE`, `DNS`, `NAT`, `CIDR`, `VPS`, and `API`.
- Fleet dashboard polish was limited to responsive CSS; deeper visual redesign can remain a separate frontend-only pass.

### Remaining risks

- The broad one-process setup wizard suite still has an isolation/order problem that causes missing setup wizard tables after some test files run. This was not introduced by the frontend-only changes and should be handled separately.
- Full project pytest still does not complete within the 304 second execution window.
- Browser visual QA was not run in this prompt because no local server was requested or started; route/render tests and JS checks were used instead.

### Full honest notes

- This prompt intentionally avoided backend service edits.
- This prompt intentionally avoided any live execution path.
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

## Prompt 6 — Fleet Provisioning Dashboard

### Commit

Final commit hash is reported in the assistant final response for this prompt. Git commit hashes cannot be embedded inside the same commit without changing the hash.

### Dashboard features

- Added fleet-level provisioning service for router registry rows.
- Added aggregate fleet metrics:
  - total routers
  - reserved
  - waiting key
  - peer ready
  - VPN verified
  - fully onboarded
  - failed
  - retired
- Added VPN allocation usage calculation:
  - pool CIDR
  - server VPN IP
  - used router IPs
  - remaining router IPs
  - capacity
  - next available router IP
- Added health summary model:
  - healthy
  - stale
  - missing handshake
  - not verified
- Added action-needed list for failed/waiting/peer-ready routers.
- Added fleet dashboard UI:
  - KPI cards
  - allocation usage bar
  - filters/search
  - action-needed panel
  - router table
  - router details drawer
- Added fleet routes:
  - `GET /admin/radius/setup-wizard/fleet`
  - `GET /admin/radius/setup-wizard/fleet/data`
  - `GET /admin/radius/setup-wizard/fleet/router/<registry_id>`
  - `POST /admin/radius/setup-wizard/fleet/router/<registry_id>/resume`
  - `POST /admin/radius/setup-wizard/fleet/router/<registry_id>/retire`
- Resume/retire actions delegate to RecoveryService only.

### Fleet metrics

- 0-router dashboard shows empty metrics and next IP `10.10.0.2`.
- 50-router simulation shows:
  - `total_routers=50`
  - `reserved=50`
  - `used=50`
  - `remaining=203`
  - `next_available=10.10.0.52`

### Files changed

- `app/radius/services/setup_wizard_fleet.py`
- `app/radius/routes/setup_wizard.py`
- `app/templates/radius/setup_wizard_fleet.html`
- `app/static/css/setup_wizard_fleet.css`
- `app/static/js/setup_wizard_fleet.js`
- `docs/setup_wizard/FLEET_PROVISIONING_DASHBOARD.md`
- `tests/test_router_fleet_dashboard.py`
- `docs/setup_wizard/EXECUTION_LOG.md`

### Tests exact results

- `python -m compileall app` passed.
- `node --check app/static/js/setup_wizard_fleet.js` passed.
- Initial `python -m pytest tests/test_router_fleet_dashboard.py -q`:
  - Result: 1 failed, 8 passed.
  - Failure was a test expectation: router labels are safely slugged by the provisioning service.
- Final `python -m pytest tests/test_router_fleet_dashboard.py -q`:
  - Result: 9 passed, 1841 warnings in 8.98s.
- `python -m pytest tests/test_router_provisioning_orchestrator.py -q`:
  - Result: 12 passed, 2592 warnings in 13.09s.
- Setup wizard related suite:
  - Command:
    `python -m pytest tests/test_setup_wizard_foundation.py tests/test_setup_wizard_internet_planner.py tests/test_setup_wizard_vpn_radius_planner.py tests/test_setup_wizard_hotspot_planner.py tests/test_setup_wizard_broadband_planner.py tests/test_setup_wizard_routes.py tests/test_setup_wizard_verification_engine.py tests/test_setup_wizard_operational_waves.py tests/test_setup_wizard_pilot_drill.py tests/test_setup_wizard_lab_mode.py tests/test_setup_wizard_v2.py tests/test_setup_wizard_v2_hotspot_broadband.py tests/test_setup_wizard_added_services.py tests/test_server_wireguard_peer_apply.py tests/test_server_wireguard_readiness.py tests/test_server_wireguard_real_adapter.py tests/test_wireguard_peer_health.py tests/test_router_lifecycle.py tests/test_router_provisioning_orchestrator.py tests/test_setup_wizard_recovery.py tests/test_router_fleet_dashboard.py -q`
  - Result: 195 passed, 16261 warnings in 97.67s.
- `git diff --check` passed with line-ending warnings only.
- `python -m pytest -q` was attempted and timed out after about 304 seconds. No failing test output was returned before timeout.

### Safety confirmations

- No live MikroTik apply was added.
- No VPS/WireGuard mutation was added.
- No live apply was enabled.
- Fleet resume delegates to RecoveryService.
- Fleet retire delegates to RecoveryService.
- No action button applies router/VPS configuration.
- Data and router detail endpoints do not expose plaintext private keys, full public keys, Radius secrets, or API passwords.
- `radius-module-admin` was not touched.
- Flutter was not touched.
- Existing RADIUS auth/accounting behavior was not changed.

### Remaining gaps

- Health status is derived conservatively from lifecycle/peer state; it does not yet aggregate live WireGuard handshake history across the fleet.
- Router details drawer is intentionally compact and may later link into richer per-router recovery drill views.
- Retire action is available through the endpoint, but the UI currently focuses on details rather than destructive lifecycle actions.
- Full project pytest still does not complete within the 304 second execution window.

### Full honest notes

- The fleet dashboard is an internal operational view, not customer-facing one-click onboarding.
- The dashboard uses the existing provisioning registry as source of truth and does not duplicate allocation logic.
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
