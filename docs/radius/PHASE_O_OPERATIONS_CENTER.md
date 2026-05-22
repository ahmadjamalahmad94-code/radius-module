# Phase O — Router Operations Center

> A surgical hardening pass on top of Phase S. Goal: turn the
> existing admin tools into a real "operations center" that
> answers, within seconds, four questions per router:
>
>   1. Is it healthy?
>   2. Are there active problems?
>   3. Is it safe to act on it?
>   4. If something just went wrong, how do I recover?

Phase O is **composer-only**. Every new file consumes the Phase
S foundations (audit / alerts / snapshots / backups / jobs /
events / permissions / interface safety / topology / change
preview). No new business logic. No background workers. No new
router protocols. No role-editing UI. No realtime sockets.

---

## Routes added

| Route | Guard | What it does |
|---|---|---|
| `GET /admin/radius/mt/<id>/overview`          | `PERM_VIEW`         | O1 — single-router status snapshot |
| `GET /admin/radius/problems`                   | `PERM_DIAGNOSTICS`  | O3 — fleet-wide problems center |
| `GET /admin/radius/mt/<id>/timeline`           | `PERM_VIEW`         | O4 — human-readable audit timeline |
| `GET /admin/radius/recovery/<audit_id>`        | `PERM_VIEW`         | O8 — recovery plan composer |
| `GET /admin/radius/permissions`                | `PERM_AUDIT_VIEW`   | O11 — read-only permission matrix |
| `GET /admin/radius/mt/<id>/assistant`          | `PERM_VIEW`         | O12 — guided operations checklist |

Existing routes that were modified (no new routes, just
refinements):

| Route | Change |
|---|---|
| `GET/POST /admin/radius/mt/<id>/program/plan`  | O6 wires the change-preview composer |
| `POST /admin/radius/mt/<id>/program/apply`     | O5 pre-execution safety check + O7 backup-aware banner |

---

## Services added

| Module | Purpose |
|---|---|
| `mt_router_overview`   | O1 — overview composer (snapshot + alerts + backup + audit + safety reasons + suggested actions) |
| `mt_health_score`      | O2 — pure scoring over an overview → `HealthScore(state, score, reasons, primary_signal)` |
| `mt_problems`          | O3 — walk overviews, group into now/soon/info buckets |
| `mt_audit_presenter`   | O4 — map an audit row to Arabic headline + recovery hint |
| `mt_safety_check`      | O5 — `evaluate(...)` returns `SafetyCheck(allowed, severity, blocking_reasons, warnings, recommendations)` |
| `mt_change_preview`    | O6 — `preview_plan(...)` returns add/modify/remove items + impact + data-quality warnings |
| `mt_recovery_plan`     | O8 — `build_plan(audit_id)` returns suggested steps + nearest backup before the event |
| `mt_alerts_generator`  | O9 — bridge O3 problems ↔ S6 alerts_repo with auto-dedup and auto-resolve |
| `mt_topology`          | O10 — added `overlay_health(...)` hook to decorate nodes with O2 state |
| `mt_permission_matrix` | O11 — read-only matrix composer over admins × MikroTik perms |
| `mt_guided_op`         | O12 — server-rendered checklist that stitches O2/O5/O7 + audit-history |

---

## Migrations added

| File | Purpose |
|---|---|
| `042_router_backups_reason.sql` | O7 — adds `reason` column to `router_backups` (manual / scheduled / before_dangerous / before_programming / before_recovery). Default `'manual'`. |

No other Phase O step required a schema change — every composer
reads what Phase S already persists.

---

## Tests added

| Mini-phase | Test file | Count |
|---|---|---|
| O1  | `tests/test_mt_router_overview_o1.py`         | 15 |
| O2  | `tests/test_mt_health_score_o2.py`            | 17 |
| O3  | `tests/test_mt_problems_o3.py`                | 13 |
| O4  | `tests/test_mt_audit_timeline_o4.py`          | 13 |
| O5  | `tests/test_mt_safety_check_o5.py`            | 17 |
| O6  | `tests/test_mt_change_preview_o6.py`          | 9  |
| O7  | `tests/test_mt_backup_reason_o7.py`           | 6  |
| O8  | `tests/test_mt_recovery_plan_o8.py`           | 9  |
| O9  | `tests/test_mt_alerts_generator_o9.py`        | 6  |
| O10 | `tests/test_mt_topology_health_o10.py`        | 10 |
| O11 | `tests/test_mt_permission_matrix_o11.py`      | 10 |
| O12 | `tests/test_mt_guided_op_o12.py`              | 12 |

All Phase O tests are pure-Python + DB-only. None contact a
router. Full regression run after O3, O6, O9, O12 per directive.

---

## Design notes worth keeping

### Composer pattern
Every Phase O service is `function(repo state) → dataclass`. No
RouterOS calls. No `requests`/`paramiko`. This is deliberate:

- Tests stay fast and deterministic.
- Live router state is consumed via the Phase S snapshot cache
  (S7) — never re-fetched at request time.
- Adding a worker later that refreshes those snapshots is a
  drop-in change; the composer signatures don't move.

### Severity-fold "worse wins"
Used by O2 (health), O5 (safety), O4 (timeline chips). When
multiple signals stack, the worst one drives the visible state;
the others land in the `reasons` list for transparency.

### Dedup contract for auto-alerts (O9)
`dedup_key = "auto.<problem_type>:<router_id>"`. Re-running
`refresh_alerts_from_problems(tenant_id)` is idempotent:
existing keys refresh, missing keys open, gone keys resolve.

To prevent the generator from feeding its own output back as
new "you have N alerts" problems on the next pass,
`mt_router_overview._alert_counts` now skips alerts whose rule
starts with `auto.` — these are derivative, not new signals.

### Template-guard idiom
Templates that render shared partials guard new variables with
`{% if X is defined and X %}` (not `{% if X is not none %}`).
This is what unblocked the O5/O6 regressions: routes that
don't pass the new variable still render cleanly.

---

## Known limitations / explicit non-goals (still)

- **No automatic rollback.** O8 emits a plan; the operator
  still clicks through the existing tools.
- **No background worker.** Snapshots are refreshed by the
  existing S7 scheduler; O9's `refresh_alerts_from_problems`
  is a manual call (button or future cron).
- **No realtime WS/SSE.** Pages are server-rendered + reload-
  to-refresh. The S11.1 event publisher is still in place but
  Phase O does not subscribe to it.
- **No role editing UI.** O11 surfaces state only; changes
  still happen on the existing admins page.
- **Topology is still a card grid.** O10 adds a health overlay
  but does not draw a graph.
- **N+1 over a large fleet.** O3 and O10 build one overview
  per router in Python. Acceptable for tens of routers, not
  thousands — when that day comes, batch the snapshot reads.

---

## Manual QA checklist

For a fresh staging tenant with at least two routers and one
recent audit row, walk this top-to-bottom:

1. `/admin/radius/mt/<id>/overview` renders with a hero, KPI
   strip, suggested actions card. Health pill matches the
   underlying overview state.
2. `/admin/radius/problems` lists current problems in three
   buckets (now/soon/info). Filtering by router / severity /
   type narrows results without 500s.
3. `/admin/radius/mt/<id>/timeline` renders audit rows in
   readable Arabic. Partial-apply rows show a recovery hint
   that links to the recovery plan.
4. `/admin/radius/mt/<id>/program/plan` shows a "Before / After"
   preview card before the script. Apply with a missing backup
   shows the red banner; apply without permission is blocked
   server-side (route returns without touching the wire).
5. `/admin/radius/recovery/<audit_id>` renders the ordered
   recovery steps + nearest backup card + related-job link.
6. `/admin/radius/topology?health=risky` returns only
   risky-state routers; default view shows health chips.
7. `/admin/radius/permissions` shows every active admin with
   one chip per MikroTik permission. Editing link goes to
   `/admin/radius/admins`.
8. `/admin/radius/mt/<id>/assistant?op=programming_hotspot`
   shows the checklist. A disabled router blocks on health.
   A missing backup blocks programming but is informational
   for `?op=backup_save`.

If any item is red, capture the URL + screenshot before
filing — the route names + `data-mt-*` markers in the HTML
are stable identifiers.

---

## Recommended next phase

If you keep going after O, the natural next step is **Phase T
— Operator Trust**: turn the read-only advisory layers into
active assists.

- O9's `refresh_alerts_from_problems` runs on the heartbeat
  worker (cadence + back-off, not every tick).
- O5's safety check is the gate on every mutating MikroTik
  route, not just `program/apply` (currently a sample of one).
- O8 emits a one-click "apply recovery plan" job (jobs_repo
  + S1.2 runner), gated by `PERM_ROLLBACK`.
- O11 exposes a granular role editor (the still-explicit
  non-goal of Phase O).
- O12's checklist gets a follow-up post-execution panel
  ("did it work? log the outcome") that feeds back into O3.

Each of those is a Phase O–sized increment, not a rewrite.

— Generated at the end of Phase O, 2026-05-22.
