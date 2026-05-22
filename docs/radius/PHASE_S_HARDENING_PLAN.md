# Phase S — Hardening + Expansion Plan

> Mission: turn the MikroTik/NAS feature set into a **safer**,
> more scalable, more operator-friendly network operations
> platform. Each commit was scoped to one focused change with
> tests; nothing destructive ships without a confirm guard and
> an audit row.

This file logs what shipped, what was deferred (and why), and
how to verify on the VPS. The operator-facing surface guide is
in [MIKROTIK_OPERATIONS_CENTER.md](MIKROTIK_OPERATIONS_CENTER.md).

---

## Completion log

| Track | Commit  | Tests file                                | Count |
|-------|---------|-------------------------------------------|-------|
| S1.1  | `a184655` | test_jobs_repo_s1_1.py                  | 19    |
| S1.2  | `ce9aab6` | test_jobs_runner_s1_2.py                | 10    |
| S1.3  | `5ee41b0` | test_jobs_diagnostics_s1_3.py           | 10    |
| S2.1  | `12d7679` | test_audit_log_ext_s2_1.py              | 11    |
| S2.2  | `1951641` | test_audit_log_ui_s2_2.py               | 10    |
| S2.3  | `304d479` | test_audit_wiring_s2_3.py               | 4     |
| S3.1  | `1b6f1f5` | test_mt_permissions_s3_1.py             | 9     |
| S3.2  | `21b77f2` | test_route_permissions_s3_2.py          | 9     |
| S3.3  | `7ea9d70` | test_mt_scope_s3_3.py                   | 8     |
| S4.1  | `c0c9087` | test_mt_interface_safety_s4_1.py        | 14    |
| S4.2  | `6eb05d9` | test_mt_programming_hardening_s4_2.py   | 7     |
| S4.3  | `2ecf7e5` | test_apply_partial_s4_3.py              | 7     |
| S5.1  | `f31a061` | test_mt_topology_s5_1.py                | 8     |
| S5.2+S5.3 | `b8c3cc4` | test_mt_topology_ui_s5_2.py         | 7     |
| S6.1  | `9bbb68f` | test_alerts_repo_s6_1.py                | 10    |
| S6.2  | `c70e692` | test_mt_alerts_ui_s6_2.py               | 8     |
| S7    | `f35433f` | test_router_snapshots_s7.py             | 11    |
| S8    | `29f3d0b` | test_mt_backups_s8.py                   | 9     |
| S11.1 | `524adb9` | test_events_publisher_s11_1.py          | 8     |

**Total Phase S commits: 19. Total Phase S tests: ~169.**

---

## Migrations added

| # | Filename | What it adds |
|---|----------|--------------|
| 037 | `037_jobs.sql` | generic background-job table |
| 038 | `038_audit_log_ext.sql` | severity / result_status / router_id / error_message / before_json / after_json on `audit_log` |
| 039 | `039_alerts.sql` | smart-alert storage with dedup |
| 040 | `040_router_snapshots.sql` | per-router snapshot cache (counters + resource + last_success/last_error) |
| 041 | `041_router_backups.sql` | backup metadata |

All idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ALTER TABLE ADD COLUMN` with defaults). Down-migrations not provided — additive only.

---

## Safety contracts pinned this phase

1. **Redaction at the boundary.** Every JSON blob written to
   `jobs.payload_json` / `jobs.result_json` / `audit_log.payload_json`
   / `audit_log.before_json` / `audit_log.after_json` /
   `alerts.evidence_json` / `router_snapshots.counters_json` /
   `router_snapshots.resource_json` runs through the shared
   `_redact()` helper before storage. Tests assert this for each
   path. Adding a new column? Pipe it through `_redact`.

2. **Permission gate at every dangerous route.** S3.2 wraps
   operations center, audit log, programming, login designer,
   backups, and jobs routes with `requires_perm(...)`. Super
   admins still see everything (back-compat). Non-admins see
   403 HTML or 403 JSON depending on Accept header — never a
   login redirect.

3. **Interface safety classifier before apply.** S4.1+S4.2
   block WireGuard / management / WAN interfaces from ever
   being targets of programming. If the K4 readers can't fetch
   routes, the classifier degrades silently (no false-WAN flag).

4. **Partial-apply visibility.** S4.3 distinguishes "nothing
   applied" from "some applied then failed." The latter writes
   `audit_log.result_status='partial'`, `severity='warning'`,
   and shows the operator a recovery hint pointing at Q4
   unprogram.

5. **No router contact inside page renders.** S5.1 + S7 make
   the topology and operations pages read from cached
   snapshots. Polling lives in `snapshot_refresh.refresh_fleet()`
   which is sequential, timeout-safe, and skips disabled
   routers.

6. **No fake buttons.** Where a destructive action isn't fully
   built yet (S8.4 restore-apply), the button renders with
   `disabled` + a clear Arabic reason — not a fake action that
   silently does nothing.

---

## Deferred — and why

These pieces were intentionally NOT shipped this phase. Each
has its own follow-up commit pattern.

| Item | Reason | Path to ship later |
|------|--------|--------------------|
| **S6.3** alert scan job | Foundation works; scheduling cadence + which-routers-when needs a product decision (every minute? on demand only?) | Register a scan handler with `jobs_runner.register_handler("mt.alerts.scan")` + add a tiny `cron` / scheduled invocation. The repo's dedup contract already keeps scans cheap. |
| **S8 backup file download** | S8.2 records metadata + tells the router to save; the bytes stay on the router. Streaming them through HobeRadius needs a sensitive-file ACL layer beyond `PERM_BACKUP`. | Add `GET /mt/<id>/backups/<bid>/download` gated by a stricter perm, stream via K8.1b's existing helper. |
| **S8.4 restore-apply** | Restore is destructive. The planner ships (file inspect + checksum + binary detection). Apply is a separate audited workflow. | Add a confirm-checkbox + two-step modal + dedicated audit action `mt.backup.restore`. |
| **S9 customer portal** | No real per-customer ownership column on `nas_devices` yet — only tenant_id. Shipping a "customer view" without that model would lie about scope. | First migration: add `nas_devices.customer_id` + `customer_admins` join. Then a read-only customer route + view. |
| **S10 UX polish** | Most polish (Arabic copy, empty states, disabled-with-reason buttons, severity colors) shipped inline with each track. A dedicated "UX-polish-pass" commit was deferred to avoid blast-radius on unrelated pages. | Spot-fix per ticket as operators flag rough edges. |
| **S11.2 SSE endpoint** | Streaming over Flask + Gunicorn + nginx needs a deployment review (worker class, buffering, keep-alive). The S11.1 publisher is the foundation; the endpoint comes after the deploy review. | Add `/admin/radius/events/stream` returning `text/event-stream`, gated by PERM_VIEW. Use `events_publisher.subscribe()` to feed it. Configure Gunicorn `gevent` worker. |
| **S11.3 UI progressive enhancement** | Without S11.2 there's no live channel to enhance. | Once S11.2 lands, dashboard JS subscribes; existing polling stays as fallback. |

---

## VPS deployment

```bash
ssh root@187.77.70.18
sudo bash /opt/hoberadius/deploy/deploy.sh upgrade
```

Migrations apply on container boot. No env var changes required
for Phase S to work in default mode. Optional env tunings:

| Env | Default | What it does |
|-----|---------|--------------|
| `HOBERADIUS_BACKUP_DIR` | `/tmp/hr-backups` | Filesystem path the backup planner uses (S8). |
| `HOBERADIUS_WG_SUBNET` | (unset) | When set, the S4.1 classifier blocks interfaces with addresses inside this subnet. |

### Post-upgrade verification

Run these as a super-admin after the upgrade:

1. **Containers healthy:** `docker compose ps` — all green.
2. **Migrations applied:** logs should show `applied 41 migration(s)`.
3. **App boots:** `http://187.77.70.18/admin/radius/` loads.
4. **Operations center:** `/admin/radius/mt/operations` lists routers.
5. **Router dashboard:** `/admin/radius/mt/<id>/dashboard` opens with tabs.
6. **Audit log:** `/admin/radius/audit` renders (gated by `PERM_AUDIT_VIEW`).
7. **Topology:** `/admin/radius/topology` renders.
8. **Alerts:** `/admin/radius/alerts` renders (gated by `PERM_DIAGNOSTICS`).
9. **Backups:** `/admin/radius/mt/<id>/backups` renders + restore button is **disabled** with the Arabic reason banner.
10. **Programming safety:** at `/mt/<id>/program`, trying to pick a WireGuard / management interface produces a BLOCKED risk row + apply button stays disabled.
11. **Job endpoint:** `POST /admin/radius/jobs/diagnostics/<id>` with CSRF → redirects to `/admin/radius/jobs/<job_id>` showing success or skipped.
12. **No secrets visible in HTML:** spot-check the audit detail page on an `mt.programming.hotspot.apply` row — the `payload` block should show `"***"` where the API password used to be.
13. **`mikrotik_configs` is gone:** sidebar must not list it; `/admin/radius/mt/configs` returns 410 (from N2).

### Known limitations the operator should remember

- **Restore is preview-only.** Uploading a `.backup` shows
  metadata + checksum; the apply button is disabled. Don't
  promise restore-from-backup to customers yet.
- **Snapshots are pull-only.** `snapshot_refresh.refresh_fleet()`
  isn't on a timer — call it from a cron or the operator can
  trigger it (future commit will add the endpoint). Pages
  render the latest cached snapshot regardless.
- **Customer portal foundation isn't tenant-isolated.** S9 needs
  a real `customer_id` column before it's safe to ship a
  read-only customer view.
- **S11.2 SSE not yet active.** `events_publisher` fires events
  in-process but no streaming endpoint consumes them yet. JS UIs
  still poll (existing R5 dashboard polling unchanged).
- **`pytest-randomly` re-ordering breaks the test isolation
  contract.** Always run `python -m pytest -p no:randomly`.

---

## Next recommended phase

If a Phase T comes next, the natural order is:

1. **T1**: ship S8 backup file download + ACL.
2. **T2**: ship S11.2 (SSE) + S11.3 (UI progressive enhancement).
3. **T3**: ship S6.3 (alert scan job) on a 60s timer.
4. **T4**: add `nas_devices.customer_id` migration + S9 customer portal.
5. **T5**: S8.4 restore-apply with dedicated audited workflow.

Each of these is a one-track commit that builds on the
foundations already in place — no new architecture needed.
