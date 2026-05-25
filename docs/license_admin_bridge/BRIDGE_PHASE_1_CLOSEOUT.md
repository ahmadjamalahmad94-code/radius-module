# V40 Admin Bridge Phase 1 Closeout

Date: 2026-05-25
Scope: `radius-module` backend only.

This report closes the P01-P15 backend bridge sequence. It is an engineering
readiness report, not a production activation approval. `radius-module-admin`
was treated as read-only reference, Flutter work was deferred, and live
RADIUS/MikroTik/FreeRADIUS behavior was not changed by this bridge phase.

## Executive Verdict

Phase 1 is ready as a guarded backend foundation for internal integration
testing with the V40 admin panel.

It is not yet ready as a fully automated production control plane. Several
admin endpoint contracts are still assumed rather than confirmed, service
activation remains mostly dry-run/local, restore apply remains blocked, and
UI/Flutter surfaces are deferred.

## Completed Backend Capabilities

1. Admin bridge client foundation
   - Environment-driven and disabled unless configured.
   - No network calls at application startup.
   - Mockable transport.
   - Timeout support.
   - Safe error states for disabled, missing config, timeout, and admin outage.

2. License and capacity snapshots
   - Local snapshot table for license and capacity contract responses.
   - Payload validation and normalized statuses.
   - Stale snapshot handling.
   - Sanitized payload persistence.

3. Usage metering report foundation
   - Local usage payload builder.
   - Manual dry-run endpoint.
   - Optional admin send path only when bridge config is explicitly enabled.
   - Attempt persistence.

4. Capacity enforcement foundation
   - Backend checks for subscribers, card generation, NAS, profiles, and print
     templates.
   - Existing API/domain behavior remains, but creation paths can now consult
     the stored capacity contract.
   - Capacity status endpoint for future UI clients.

5. Instance health heartbeat
   - Manual one-shot heartbeat endpoint.
   - Dry-run by default.
   - Read-only local health payload.
   - Attempt persistence.

6. Backup upload foundation
   - Manual latest-backup upload surface.
   - Dry-run and metadata-only by default.
   - Content upload is gated by explicit server-side env.
   - Existing backup flows are not replaced.

7. Restore polling workflow
   - Polls and records restore requests.
   - Creates local pre-restore SQLite snapshots.
   - Destructive restore remains blocked.
   - Status callback support exists behind bridge config.

8. Service activation polling framework
   - Polls and records admin activation jobs.
   - Dry-run by default.
   - Unsupported jobs are recorded safely.
   - Public IP change adapter is dry-run only.

9. Operations event log
   - Local bridge event persistence.
   - Read-only events API.
   - Admin callback delivery intentionally not implemented until the V40
     operations event endpoint is confirmed.

10. RADIUS accounting response path documentation
    - Confirms the existing ACK path remains unchanged.
    - Tests guard that accounting writers do not emit malformed responses.

11. Accounting events and usage counters
    - Local accounting-event ingestion/read API over `radacct`.
    - Usage summaries for tenant, subscriber, plan/profile, and NAS.
    - Advisory quota decisions only; no disconnects or auth enforcement.

## Dry-Run or Contract-Only Capabilities

- Usage report endpoint defaults to dry-run.
- Heartbeat endpoint defaults to dry-run.
- Backup upload endpoint defaults to dry-run and metadata-only.
- Service activation poll defaults to dry-run.
- Public IP change adapter is dry-run only.
- Restore apply is blocked.
- Quota decisions are advisory only.
- Operations event admin callback is local-only pending admin contract.

## Real Live Capabilities Added

The bridge phase added no automatic production control loop and no live
MikroTik/RADIUS/CoA mutation.

The only optional external admin-panel sends are manual HTTP calls through the
bridge client when:

- `HOBERADIUS_ADMIN_BRIDGE_ENABLED=true`;
- `HOBERADIUS_ADMIN_BASE_URL` is configured;
- a license key is configured;
- the specific endpoint is called by an authenticated API client; and
- the endpoint request opts out of dry-run where applicable.

## Route Map

All routes below are under `/api/v1` and require the existing API token guard.

| Method | Route | Purpose | Mutation | Default safety |
|---|---|---|---|---|
| GET | `/system/admin-bridge/capacity-status` | Local capacity state for UI | Read-only | No remote call |
| POST | `/system/admin-bridge/usage-report` | Build/send usage report | Local attempt; optional remote | Dry-run default |
| POST | `/system/admin-bridge/heartbeat` | Build/send health heartbeat | Local attempt; optional remote | Dry-run default |
| POST | `/system/admin-bridge/backups/upload-latest` | Upload latest backup metadata/content | Local attempt; optional remote | Dry-run, metadata-only default |
| POST | `/system/admin-bridge/restore/poll` | Poll restore requests | Local records; optional remote poll | No restore apply |
| POST | `/system/admin-bridge/restore/<reference>/snapshot` | Create local restore safety snapshot | Local file copy only | No restore apply |
| POST | `/system/admin-bridge/service-activations/poll` | Poll activation jobs | Local records/executions | Dry-run default |
| GET | `/system/admin-bridge/events` | Inspect local bridge events | Read-only | No remote callback |
| POST | `/accounting/events` | Record accounting event to `radacct` | Local accounting DB write | No RADIUS packet-path change |
| GET | `/accounting/usage/tenant` | Usage summary | Read-only | Local only |
| GET | `/accounting/usage/subscribers/<username>` | Subscriber usage summary | Read-only | Local only |
| GET | `/accounting/usage/plans/<plan_id>` | Plan usage summary | Read-only | Local only |
| GET | `/accounting/quota/check` | Advisory quota decision | Read-only | No enforcement |

## Environment Checklist

Required for any admin-panel remote bridge call:

- `HOBERADIUS_ADMIN_BRIDGE_ENABLED=true`
- `HOBERADIUS_ADMIN_BASE_URL=<admin base URL>`
- `HOBERADIUS_LICENSE_KEY=<instance license>` or
  `INSTANCE_LICENSE_KEY=<instance license>`
- Optional `HOBERADIUS_ADMIN_SHARED_SECRET=<shared secret>`
- Optional `HOBERADIUS_ADMIN_TIMEOUT_SECONDS`
- Optional `HOBERADIUS_ADMIN_RETRY_COUNT`

Backup content upload additionally requires:

- `HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_ENABLED=true`
- `HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES` sized for the lab payload.

## Security and Safety Guarantees Verified in Phase 1

- Bridge client is disabled by default.
- No bridge network I/O occurs on app startup.
- Secrets are masked before bridge snapshot persistence and event/report
  surfaces where bridge helpers handle payloads.
- API responses avoid raw license keys/admin shared secrets.
- Restore apply remains blocked.
- Public IP change remains dry-run only.
- Quota hook is advisory and does not disconnect users.
- No `radius-module-admin` files were modified.
- No Flutter files were modified.
- Existing RADIUS auth/accounting packet behavior was not changed.

## Admin-Panel Follow-Ups

The following require V40 admin confirmation before production rollout:

- Canonical license check path and authentication scheme.
- Capacity contract field names and feature-state shape.
- Usage-report endpoint and metric schema.
- Heartbeat endpoint payload contract.
- Backup upload size limits, retention, encryption, and checksum semantics.
- Restore poll/status endpoint state machine and human confirmation model.
- Service activation poll/status state machine and idempotency behavior.
- Operations event callback endpoint, severity values, and retry policy.

The live tracker is `docs/license_admin_bridge/CODEX_FOLLOWUPS.md`.

## Radius-Module Next Slices

1. Add a scheduler/worker only after admin endpoint contracts are confirmed.
2. Add admin callback delivery for bridge events after endpoint confirmation.
3. Add stricter idempotency-key persistence for every remote call.
4. Expand contract tests against a mocked V40 admin server fixture.
5. Review any future capacity hard-blocks with support and billing policy.
6. Keep restore apply as a separate destructive-operation project.

## Flutter Next Slices

Flutter work was intentionally deferred from this backend-only bridge run.
Future UI should consume:

- `GET /api/v1/system/admin-bridge/capacity-status`
- local warnings and feature states;
- upgrade/locked hints;
- bridge stale/degraded status;
- read-only operations events.

No Flutter code was touched in Phase 1.

## Production Deployment Checklist

Before deploying bridge automation beyond manual dry-runs:

1. Confirm all V40 admin endpoint paths and payload contracts.
2. Configure secrets through deployment secret storage only.
3. Run contract tests against a staging V40 admin panel.
4. Verify remote call idempotency and retry behavior.
5. Verify logging masks license/admin secrets.
6. Confirm backup upload size and retention policy.
7. Keep restore apply disabled until a separate destructive-operation review.
8. Verify customer-facing UI does not claim unsupported live services.
9. Run targeted bridge suite and broader API regression suite.
10. Document support escalation path for admin-panel outage/stale state.

## Final Readiness Scores

| Area | Score | Rationale |
|---|---:|---|
| Backend architecture | 84 | Clear service boundaries and route surfaces exist. Scheduler and remote callbacks remain future work. |
| Safety | 88 | Disabled/dry-run defaults are strong. Remote contract ambiguity remains the main risk. |
| Admin integration readiness | 62 | Local client and payloads exist, but admin endpoints need confirmation. |
| Restore readiness | 45 | Poll/snapshot/status exist; destructive restore is correctly blocked. |
| Service activation readiness | 58 | Framework exists; real adapters are deferred except safe dry-run public IP plan. |
| Customer production readiness | 40 | Backend foundation only; admin contracts, Flutter UX, and operational training remain. |

## Final Verdict

GO for controlled backend integration testing against a mocked or staging V40
admin panel.

NO-GO for production customer automation until V40 admin contracts, scheduler
wiring, UI disclosure, backup policy, and destructive restore/service activation
operations are separately reviewed and tested.
