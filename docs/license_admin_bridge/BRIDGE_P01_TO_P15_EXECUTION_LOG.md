# HobeRadius V40 Bridge Execution Log

## P01 — V40 Contract Audit, Read-Only

- Start time: 2026-05-25 11:10:19 +03:00
- End time: 2026-05-25 11:17:39 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `docs/license_admin_bridge/V40_CONTRACT_AUDIT.md`
  - `docs/license_admin_bridge/CODEX_FOLLOWUPS.md`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/setup_wizard/EXECUTION_LOG.md`
  - removed premature P02-scope files from the previous out-of-sequence commit:
    - `app/radius/db/migrations/065_license_admin_bridge_snapshots.sql`
    - `app/radius/services/admin_panel_client.py`
    - `docs/license_admin_bridge/P01_V40_CONTRACT_AUDIT.md`
    - `tests/test_license_admin_bridge_client.py`
- What was implemented:
  - Read the sequence file and followed the actual P01 text.
  - Created a documentation-only V40 bridge contract audit.
  - Documented expected request/response contracts, auth expectations,
    timeout/retry policy, idempotency needs, risk levels, and first milestones.
  - Created/updated the Codex follow-up tracker for admin-side gaps.
  - Corrected the previous out-of-sequence P01/P02 mix by removing premature
    runtime bridge code from this P01 slice.
- What was intentionally not implemented:
  - No AdminPanelClient runtime code in P01.
  - No migrations in P01.
  - No license/capacity snapshot runtime fetch in P01.
  - No entitlement enforcement.
  - No backup upload.
  - No restore polling.
  - No service activation polling.
  - No heartbeat.
  - No Flutter changes.
- Tests/verification:
  - `git status --short --untracked-files=all` before P01: clean.
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_api_auth_security.py -q` passed: 10 passed, 278 warnings in 1.89s.
  - `git diff --check` passed.
- Full pytest status:
  - `python -m pytest -q` attempted.
  - Result: timed out after 304043 ms.
  - No visible failure output was returned before timeout.
- Timeout notes if any:
  - Full pytest did not complete inside the 300 second execution window.
  - Recommended later chunk: run API, setup wizard, business OS, and web UI suites separately.
- Admin endpoint gaps:
  - Canonical license endpoint path is ambiguous.
  - Admin auth scheme is unconfirmed.
  - Capacity, heartbeat, backup, restore, and service activation endpoint
    schemas need admin confirmation.
- Codex follow-ups added:
  - See `docs/license_admin_bridge/CODEX_FOLLOWUPS.md`.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - P01 is contract-only. Runtime behavior is intentionally unchanged.
  - P02 must add the bridge client/snapshot foundation in a separate commit.
- GO/NO-GO for next prompt:
  - GO for P02.

## P02 — AdminPanelClient + License/Capacity Snapshot Foundation

- Start time: 2026-05-25 11:19:30 +03:00
- End time: 2026-05-25 11:27:27 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/radius/db/migrations/065_license_admin_bridge_snapshots.sql`
  - `app/radius/services/admin_panel_client.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `tests/test_license_admin_bridge_client.py`
- What was implemented:
  - Added env/config support for:
    - `HOBERADIUS_ADMIN_BRIDGE_ENABLED`
    - `HOBERADIUS_ADMIN_BASE_URL`
    - `HOBERADIUS_LICENSE_KEY`
    - `INSTANCE_LICENSE_KEY`
    - `HOBERADIUS_ADMIN_SHARED_SECRET`
    - `HOBERADIUS_ADMIN_TIMEOUT_SECONDS`
    - `HOBERADIUS_ADMIN_RETRY_COUNT`
  - Added `AdminPanelClient` with mockable transport.
  - Added license snapshot fetch/store.
  - Added capacity contract fetch/store.
  - Added local sanitized snapshot persistence.
  - Added `get_current_license_state()`.
  - Added `get_current_capacity_contract()`.
  - Added safe stale/degraded/unknown behavior when the admin panel is down or disabled.
  - Added invalid-payload rejection.
- What was intentionally not implemented:
  - No entitlement enforcement.
  - No subscriber/card/NAS blocking.
  - No backup upload.
  - No restore polling.
  - No service activation polling.
  - No heartbeat.
  - No Flutter changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_bridge_client.py -q` passed: 7 passed, 415 warnings in 9.72s.
  - `python -m pytest tests/test_api_auth_security.py -q` passed: 10 passed, 279 warnings in 3.44s.
  - `git diff --check` passed.
- Full pytest status:
  - `python -m pytest -q` attempted.
  - Result: timed out after 304043 ms.
  - No visible failure output was returned before timeout.
- Timeout notes if any:
  - Full pytest did not complete inside the 300 second execution window.
- Admin endpoint gaps:
  - Existing P01 admin endpoint gaps still apply.
  - Runtime client uses the P02 prompt paths:
    - `/api/license/check`
    - `/api/integration/hoberadius/capacity-contract`
- Codex follow-ups added:
  - No new admin follow-ups beyond P01.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Bridge client is disabled by default.
  - No admin calls occur at app startup.
  - Snapshot payloads are sanitized before storage.
  - Admin outage returns stale/unknown state and does not break local runtime.
- GO/NO-GO for next prompt:
  - GO for P03.

## P03 — Usage Metering Report to V40

- Start time: 2026-05-25 11:29:17 +03:00
- End time: 2026-05-25 11:39:55 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/db/migrations/066_license_admin_usage_reports.sql`
  - `app/radius/services/license_admin_usage_metering.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/USAGE_METERING.md`
  - `tests/test_license_admin_usage_metering.py`
- What was implemented:
  - Added `UsageMeteringService`.
  - Added normalized usage payload generation.
  - Added dry-run-first manual report route:
    `POST /api/v1/system/admin-bridge/usage-report`.
  - Added persistence for usage report attempts.
  - Added stable idempotency key per tenant/window/metrics.
  - Added safe disabled/config-missing behavior.
  - Added mocked remote-send failure handling.
  - Added usage metering documentation.
- What was intentionally not implemented:
  - No capacity enforcement.
  - No limit blocking.
  - No Flutter changes.
  - No `radius-module-admin` changes.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - First targeted P03 test run failed, uncovering two P03-scope issues:
    - test seed card lacked a required card password
    - `admins` table does not have `tenant_id`
  - Both were fixed narrowly.
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_usage_metering.py -q` passed: 6 passed, 424 warnings in 9.29s.
  - `python -m pytest tests/test_api_customer_contracts.py::test_contract_routes_are_registered tests/test_api_auth_security.py -q` passed: 11 passed, 280 warnings in 4.04s.
  - `git diff --check` passed.
- Full pytest status:
  - `python -m pytest -q` attempted after targeted fixes.
  - Result: timed out after 304031 ms.
  - No visible failure output was returned before timeout.
- Timeout notes if any:
  - Full pytest did not complete inside the 300 second execution window.
- Admin endpoint gaps:
  - Usage report endpoint path is assumed as `/api/integration/hoberadius/usage-report`.
  - This should be confirmed on the admin side before enabling remote send.
- Codex follow-ups added:
  - Usage report endpoint confirmation remains a P03 follow-up.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Manual route defaults to dry-run.
  - Remote sending requires explicit `dry_run=false` plus enabled bridge config.
  - No tests make live admin calls.
  - Usage metrics are counts only and should not expose secrets.
- GO/NO-GO for next prompt:
  - GO for P04.

## P04 — Backend Capacity Enforcement for Easy Limits

- Start time: 2026-05-25 11:45:00 +03:00
- End time: 2026-05-25 11:59:55 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/accounts.py`
  - `app/api/v1/cards.py`
  - `app/api/v1/nas.py`
  - `app/api/v1/print_templates.py`
  - `app/api/v1/profiles.py`
  - `app/radius/services/license_admin_capacity.py`
  - `app/radius/services/license_admin_usage_metering.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/CODEX_FOLLOWUPS.md`
  - `tests/test_license_admin_capacity_enforcement.py`
- What was implemented:
  - Added `CapacityEnforcementService`.
  - Added backend create guards for subscribers, card generation, NAS,
    profiles, and print templates.
  - Added JSON error details with `feature_key`, `current_usage`, `limit`,
    warnings, and contract status.
  - Added stale-contract enforcement warnings.
  - Added non-blocking no-contract behavior.
  - Corrected usage metering print template count to use
    `card_print_templates`.
- What was intentionally not implemented:
  - No Flutter capacity UX.
  - No radius-module-admin changes.
  - No remote admin call during enforcement.
  - No live RADIUS, MikroTik, FreeRADIUS, CoA, or disconnect behavior.
  - No enforcement for surfaces without a local create route in this slice.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_capacity_enforcement.py -q`
    passed: 7 passed, 500 warnings in 13.79s.
  - `python -m pytest tests/test_api_customer_contracts.py::test_contract_routes_are_registered tests/test_api_auth_security.py -q`
    passed: 11 passed, 280 warnings in 6.47s.
  - `git diff --check` passed.
- Full pytest status:
  - `python -m pytest -q` attempted.
  - Result: timed out after 308040 ms.
  - No visible failure output was returned before timeout.
- Timeout notes if any:
  - Full pytest did not complete inside the roughly 300 second execution
    window.
- Admin endpoint gaps:
  - Capacity contract field names and feature-state shape still need V40 admin
    confirmation.
- Codex follow-ups added:
  - Added P04 follow-ups for capacity field names, feature-state shape, and
    NAS/router mapping.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Stale last-successful capacity contracts are enforced with a warning.
  - Missing capacity contracts are degraded/non-blocking by design.
  - Enforcement uses local counts only; remote refresh is not performed inside
    create requests.
- GO/NO-GO for next prompt:
  - GO for P05 if the prompt boundary allows Flutter work; otherwise stop for
    boundary clarification.

## P05 — Flutter Capacity UX and Locked/Upgrade Surfaces

- Start time: 2026-05-25 12:08:00 +03:00
- End time: 2026-05-25 12:09:02 +03:00
- Commit: Not completed. No implementation commit was created for P05.
- Files changed:
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
- What was implemented:
  - Nothing. P05 was stopped before implementation.
- What was intentionally not implemented:
  - No Flutter UI changes.
  - No backend API additions.
  - No radius-module-admin changes.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - Not run for P05 because implementation was stopped at the boundary check.
- Full pytest status:
  - Not run for P05.
- Timeout notes if any:
  - None for P05.
- Admin endpoint gaps:
  - None added for P05.
- Codex follow-ups added:
  - None. This is a local scope conflict, not a radius-module-admin endpoint gap.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No. P05 requires `radius-module-app` Flutter UI, but the active master
    boundary for this run says to work only inside `radius-module`.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - P05 cannot be completed under the current "radius-module only" boundary
    because its prompt scope explicitly includes `radius-module-app` Flutter UI.
  - Proceeding would require explicit permission to work in the Flutter app, or
    a revised backend-only P05 prompt.
- GO/NO-GO for next prompt:
  - NO-GO. Stop for user clarification before P06.

## P05B — Backend Capacity Status and Upgrade Intent Surfaces

- Start time: 2026-05-25 12:29:00 +03:00
- End time: 2026-05-25 12:49:58 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/services/license_admin_capacity.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/CAPACITY_STATUS_API.md`
  - `tests/test_license_admin_capacity_status.py`
- What was implemented:
  - Added read-only backend capacity status endpoint:
    `GET /api/v1/system/admin-bridge/capacity-status`.
  - Exposed local usage, stored limits, feature states, contract status,
    stale/degraded warnings, and UI-safe Arabic hints.
  - Added local-only upgrade intent metadata marked as dry-run/local intent.
  - Added API documentation for future Flutter/web consumers.
- What was intentionally not implemented:
  - No Flutter UI.
  - No radius-module-admin changes.
  - No remote admin call from the capacity status request path.
  - No actual upgrade/payment/service request creation.
  - No new capacity enforcement beyond P04.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_capacity_status.py -q`
    passed: 6 passed, 428 warnings in 14.49s.
  - `python -m pytest tests/test_license_admin_capacity_enforcement.py -q`
    passed: 7 passed, 500 warnings in 16.32s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_usage_metering.py -q`
    passed: 13 passed, 845 warnings in 20.40s.
  - `git diff --check` passed.
- Full pytest status:
  - Not run for P05B; targeted bridge/capacity tests passed.
- Timeout notes if any:
  - None for P05B targeted tests.
- Admin endpoint gaps:
  - No new admin endpoint gaps. Upgrade intent remains local-only.
- Codex follow-ups added:
  - None.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No. Original Flutter P05 remains deferred to a dedicated
    `radius-module-app` wave.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Capacity status is based on the last local snapshot and local counts only.
  - UI clients must continue treating backend enforcement errors as the source
    of truth.
  - Upgrade intent is local metadata only and must not be represented as a paid
    or admin-submitted request.
- GO/NO-GO for next prompt:
  - GO for P06.

## P06 — Instance Health Heartbeat to V40

- Start time: 2026-05-25 12:50:30 +03:00
- End time: 2026-05-25 13:05:14 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/db/migrations/067_license_admin_heartbeat_attempts.sql`
  - `app/radius/services/admin_panel_client.py`
  - `app/radius/services/license_admin_instance_health.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/INSTANCE_HEALTH_HEARTBEAT.md`
  - `tests/test_license_admin_instance_health.py`
- What was implemented:
  - Added `InstanceHealthService`.
  - Added heartbeat attempt persistence.
  - Added `AdminPanelClient.post_instance_heartbeat()`.
  - Added manual dry-run-first API route:
    `POST /api/v1/system/admin-bridge/heartbeat`.
  - Added read-only health payload fields for DB, accounting, backup,
    scheduler workers, storage, bridge sync state, warnings, and generated time.
  - Added heartbeat documentation.
- What was intentionally not implemented:
  - No scheduler hook was added in P06.
  - No service restarts.
  - No shell probes.
  - No radius-module-admin changes.
  - No Flutter changes.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_instance_health.py -q`
    passed: 6 passed, 425 warnings in 4.59s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_usage_metering.py tests/test_license_admin_capacity_status.py tests/test_license_admin_capacity_enforcement.py -q`
    passed: 26 passed, 1798 warnings in 16.30s.
  - `git diff --check` passed.
- Full pytest status:
  - `python -m pytest -q` attempted.
  - Result: timed out after 308028 ms.
  - No visible failure output was returned before timeout.
- Timeout notes if any:
  - Full pytest did not complete inside the roughly 300 second execution
    window.
- Admin endpoint gaps:
  - Heartbeat endpoint path is assumed as
    `/api/integration/hoberadius/instance-ops/heartbeat`.
  - P01 follow-up already tracks confirmation for this endpoint.
- Codex follow-ups added:
  - None in P06.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Heartbeat is dry-run by default.
  - Remote sending requires enabled bridge config.
  - FreeRADIUS status remains unknown unless a future safe read-only probe is
    defined.
  - Scheduler wiring remains deferred to a later safe scheduler prompt.
- GO/NO-GO for next prompt:
  - GO for P07.

## P07 — Backup Upload Agent Foundation

- Start time: 2026-05-25 13:05:45 +03:00
- End time: 2026-05-25 13:15:33 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/db/migrations/068_license_admin_backup_uploads.sql`
  - `app/radius/services/admin_panel_client.py`
  - `app/radius/services/license_admin_backup_upload.py`
  - `docs/license_admin_bridge/BACKUP_UPLOAD_AGENT.md`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `tests/test_license_admin_backup_upload.py`
- What was implemented:
  - Added bridge backup artifact metadata table.
  - Added bridge backup upload attempts table.
  - Added SHA-256 checksum calculation.
  - Added `BackupUploadService`.
  - Added `AdminPanelClient.post_backup_upload()`.
  - Added manual dry-run-first route:
    `POST /api/v1/system/admin-bridge/backups/upload-latest`.
  - Added metadata-only upload default.
  - Added content upload opt-in gate and size cap.
  - Added backup upload documentation.
- What was intentionally not implemented:
  - No restore execution.
  - No backup deletion.
  - No changes to existing local/Google Drive backup flows.
  - No radius-module-admin changes.
  - No Flutter changes.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_backup_upload.py -q`
    passed: 7 passed, 505 warnings in 5.52s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_instance_health.py tests/test_license_admin_usage_metering.py tests/test_api_customer_contracts.py::test_contract_routes_are_registered -q`
    passed: 20 passed, 1496 warnings in 13.24s.
  - `git diff --check` passed.
- Full pytest status:
  - `python -m pytest -q` attempted.
  - Result: timed out after 308052 ms.
  - No visible failure output was returned before timeout.
- Timeout notes if any:
  - Full pytest did not complete inside the roughly 300 second execution
    window.
- Admin endpoint gaps:
  - Backup upload endpoint path is assumed as
    `/api/integration/hoberadius/backups/upload`.
  - P01 follow-up already tracks confirmation for this endpoint.
- Codex follow-ups added:
  - None in P07.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Backup upload route defaults to dry-run.
  - Upload defaults to metadata-only.
  - Content upload requires explicit request and explicit env enablement.
  - Retention and deletion are not managed by this bridge layer.
- GO/NO-GO for next prompt:
  - GO for P08.

## P08 — Restore Poll and Safe Restore Workflow

- Start time: 2026-05-25 13:16:00 +03:00
- End time: 2026-05-25 13:20:45 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/db/migrations/069_license_admin_restore_requests.sql`
  - `app/radius/services/admin_panel_client.py`
  - `app/radius/services/license_admin_restore.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/RESTORE_WORKFLOW.md`
  - `tests/test_license_admin_restore_workflow.py`
- What was implemented:
  - Added restore request persistence.
  - Added `AdminPanelClient.poll_restore_requests()`.
  - Added `AdminPanelClient.post_restore_status()`.
  - Added `RestoreWorkflowService`.
  - Added safe local restore states.
  - Added idempotent restore request recording.
  - Added local pre-restore SQLite snapshot creation.
  - Added checksum verification gate.
  - Added destructive restore apply blocker.
  - Added manual restore poll and snapshot routes.
  - Added restore workflow documentation.
- What was intentionally not implemented:
  - No automatic restore execution.
  - No DB overwrite.
  - No file restore.
  - No one-click restore.
  - No radius-module-admin changes.
  - No Flutter changes.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_restore_workflow.py -q`
    passed: 8 passed, 586 warnings in 5.65s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_backup_upload.py tests/test_license_admin_instance_health.py -q`
    passed: 20 passed, 1388 warnings in 12.22s.
  - `git diff --check` passed.
- Full pytest status:
  - Not run for P08. Full pytest was attempted for P06 and P07 and timed out
    after roughly 308 seconds with no visible failure output. P08 used targeted
    restore and bridge regression tests instead.
- Timeout notes if any:
  - No P08 targeted timeout.
- Admin endpoint gaps:
  - Restore poll/status paths are assumed as:
    `/api/integration/hoberadius/backup-restore/poll`
    and `/api/integration/hoberadius/backup-restore/<reference>/status`.
  - P01 follow-up already tracks confirmation for these endpoints.
- Codex follow-ups added:
  - None in P08.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Destructive restore remains blocked by default.
  - Even with the env flag, P08 returns not implemented for apply.
  - Candidate backup download is not implemented in P08.
- GO/NO-GO for next prompt:
  - GO for P09.

## P09 — Service Activation Polling Framework

- Start time: 2026-05-25 13:25:00 +03:00
- End time: 2026-05-25 13:31:00 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/db/migrations/070_license_admin_service_activations.sql`
  - `app/radius/services/admin_panel_client.py`
  - `app/radius/services/license_admin_service_activation.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/SERVICE_ACTIVATION_AGENT.md`
  - `tests/test_license_admin_service_activation.py`
- What was implemented:
  - Added V40 service activation poll/status client methods.
  - Added local service activation execution persistence.
  - Added adapter registry contract.
  - Added safe unsupported-service recording.
  - Added idempotency by `(tenant_id, reference)`.
  - Added manual dry-run-first poll API route.
  - Added service activation agent documentation.
- What was intentionally not implemented:
  - No Public IP change adapter yet.
  - No live service execution.
  - No MikroTik, RADIUS, FreeRADIUS, VPS, or CoA mutation.
  - No radius-module-admin changes.
  - No Flutter changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_service_activation.py -q`
    passed: 6 passed, 448 warnings in 4.59s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_restore_workflow.py tests/test_license_admin_backup_upload.py tests/test_license_admin_instance_health.py -q`
    passed: 28 passed, 2001 warnings in 17.53s.
  - `git diff --check` passed with line-ending warnings only.
- Full pytest status:
  - Attempted `python -m pytest -q`.
  - Timed out after 304021 ms with no visible failure output.
- Timeout notes if any:
  - Full pytest timeout matches prior bridge-run behavior. Targeted P09 and
    bridge regression suites passed.
- Admin endpoint gaps:
  - Service activation poll/status endpoints are assumed as:
    `/api/integration/hoberadius/service-activations/poll`
    and
    `/api/integration/hoberadius/service-activations/<reference>/status`.
- Codex follow-ups added:
  - None yet in P09.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Adapter registry defaults empty, so unknown jobs are stored as
    `unsupported_service`.
  - P09 does not implement live action adapters.
- GO/NO-GO for next prompt:
  - GO for P10 after P09 commit.

## P10 — Public IP Change Service Adapter, Dry-Run First

- Start time: 2026-05-25 13:39:00 +03:00
- End time: 2026-05-25 13:45:00 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/radius/services/license_admin_public_ip_change.py`
  - `app/radius/services/license_admin_service_activation.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/PUBLIC_IP_CHANGE_ADAPTER.md`
  - `tests/test_license_admin_public_ip_change.py`
  - `tests/test_license_admin_service_activation.py`
- What was implemented:
  - Added dry-run-only `network.public_ip_change` adapter.
  - Registered service-key aliases `network` and `public_ip_change`.
  - Added payload validation for target router, router type, public IP, method,
    and WAN-interface warning.
  - Added scoped RouterOS command preview with generated bridge tag.
  - Added duplicate-reference idempotency coverage through the P09 persistence
    layer.
- What was intentionally not implemented:
  - No live MikroTik apply.
  - No site-exit policy mutation.
  - No route/NAT/firewall write execution.
  - No radius-module-admin changes.
  - No Flutter changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_public_ip_change.py tests/test_license_admin_service_activation.py -q`
    passed: 12 passed, 899 warnings in 9.08s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_restore_workflow.py -q`
    passed: 15 passed, 1039 warnings in 10.71s.
  - `git diff --check` passed with line-ending warnings only.
- Full pytest status:
  - Not rerun for P10. P09 full pytest timed out after 304021 ms with no
    visible failure output; P10 used targeted adapter and bridge regression
    tests.
- Timeout notes if any:
  - No P10 targeted timeout.
- Admin endpoint gaps:
  - No new admin endpoint beyond P09 service activation poll/status.
- Codex follow-ups added:
  - None in P10.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Adapter produces command previews only.
  - Future live apply still needs a separate guarded adapter, backup gate, and
    rollback proof.
- GO/NO-GO for next prompt:
  - GO for P11 after P10 commit.

## P11 — Operations Event Feedback Loop

- Start time: 2026-05-25 13:51:00 +03:00
- End time: 2026-05-25 13:57:00 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `app/api/v1/system.py`
  - `app/radius/db/migrations/071_license_admin_bridge_events.sql`
  - `app/radius/services/license_admin_bridge_events.py`
  - `app/radius/services/license_admin_service_activation.py`
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/license_admin_bridge/CODEX_FOLLOWUPS.md`
  - `docs/license_admin_bridge/OPERATIONS_EVENTS.md`
  - `tests/test_license_admin_bridge_events.py`
- What was implemented:
  - Added local unified bridge event table.
  - Added `BridgeEventService` with sanitized payloads, Arabic labels,
    optional idempotency keys, list, and summary.
  - Added read-only events API.
  - Added advisory service-activation event recording.
  - Added Codex follow-up for missing V40 event callback contract.
- What was intentionally not implemented:
  - No remote event callback because no canonical V40 endpoint is confirmed.
  - No business-flow blocking if local event recording fails.
  - No radius-module-admin changes.
  - No Flutter changes.
  - No live RADIUS, MikroTik, FreeRADIUS, or CoA changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_license_admin_bridge_events.py tests/test_license_admin_service_activation.py tests/test_license_admin_public_ip_change.py -q`
    passed: 18 passed, 1367 warnings in 13.18s.
  - `python -m pytest tests/test_license_admin_bridge_client.py tests/test_license_admin_restore_workflow.py -q`
    passed: 15 passed, 1053 warnings in 10.10s.
  - `git diff --check` passed with line-ending warnings only.
- Full pytest status:
  - Not planned for P11 unless targeted tests reveal a broader issue. P09 full
    pytest timed out after 304021 ms with no visible failure output.
- Timeout notes if any:
  - None yet for P11 targeted tests.
- Admin endpoint gaps:
  - V40 operations event callback endpoint is not confirmed.
- Codex follow-ups added:
  - Added P11 items to `CODEX_FOLLOWUPS.md` for event endpoint and payload
    contract confirmation.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No.
- Risk notes:
  - Event callback remains local-only until admin contract is confirmed.
- GO/NO-GO for next prompt:
  - GO for P12 after P11 commit.

## P12 — RADIUS Accounting ACK Path Stabilization

- Start time: 2026-05-25 14:03:00 +03:00
- End time: 2026-05-25 14:09:00 +03:00
- Commit: Final hash reported in assistant response; embedding the hash inside
  the same commit would change the hash again.
- Files changed:
  - `docs/license_admin_bridge/BRIDGE_P01_TO_P15_EXECUTION_LOG.md`
  - `docs/radius/ACCOUNTING_RESPONSE_PATH.md`
  - `tests/test_radius_accounting_response_path.py`
- What was implemented:
  - Audited FreeRADIUS accounting listener and Docker UDP/1813 exposure.
  - Audited accounting section behavior.
  - Added regression tests proving SQL accounting failure is non-blocking for
    ACK shape because `sql` rcodes are softened and final `ok` is present.
  - Added regression tests proving SQL auth remains disabled in authorize and
    post-auth.
  - Documented the accounting ACK path and lab evidence still required.
- What was intentionally not implemented:
  - No auth path changes.
  - No SQL auth re-enable.
  - No quota, ledger, reseller, or policy changes.
  - No FreeRADIUS live restart or live lab claim.
  - No radius-module-admin changes.
  - No Flutter changes.
- Tests/verification:
  - `python -m compileall app` passed.
  - `python -m pytest tests/test_radius_accounting_response_path.py tests/test_acct_puller_gate.py -q`
    passed: 16 passed in 0.57s.
  - `docker compose -f deploy/docker-compose.yml config` attempted and failed
    before config rendering because local `.env` is missing:
    `env file ... radius-module\.env not found`.
  - `git diff --check` passed with line-ending warnings only.
- Full pytest status:
  - Not planned for P12 because this slice uses targeted config tests. P09 full
    pytest timed out after 304021 ms with no visible failure output.
- Timeout notes if any:
  - No P12 targeted timeout.
- Admin endpoint gaps:
  - None; P12 is local FreeRADIUS/app deployment config.
- Codex follow-ups added:
  - None.
- Radius-module-admin touched? must be NO:
  - NO.
- Flutter touched? yes/no + reason:
  - No.
- Live RADIUS/MikroTik behavior changed? yes/no + reason:
  - No runtime behavior changed; P12 adds tests/docs around existing config.
- Risk notes:
  - Live packet evidence still requires a running CHR/VPS lab.
  - `radacct` persistence depends on SQLite availability and schema health.
  - Docker compose config verification requires a local `.env` file.
- GO/NO-GO for next prompt:
  - GO for P13 after P12 commit.
