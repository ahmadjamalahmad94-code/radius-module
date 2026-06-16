# API-First Parity — v1 JSON Contracts (feat/api-first-parity)

New additive `/api/v1` JSON endpoints that mirror existing server-rendered web
pages so the Flutter app can reach parity. Each endpoint reuses the existing
service/query layer (no duplicated business logic) and the standard envelope:

```json
{ "ok": true,  "data": { ... }, "meta": { "request_id": "…", "version": "v1" } }
{ "ok": false, "error": { "code": "…", "message": "…", "details": {} }, "meta": {…} }
```

Auth: `require_api_token` (Bearer / `X-API-Key` / admin Basic) — the API auth
model, matching the web page's access tier. All routes are tenant-scoped via
`g.tenant_id`.

---

## Group 1 — Subscriber Groups
Mirrors `routes/subscriber_groups.py` (web `/admin/radius/subscriber-groups`,
web view perm `users.view`). Reuses `SubscriberGroupsService` + the same
online-sessions / users services. File: `app/api/v1/subscriber_groups.py`.

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/api/v1/subscriber-groups` | `sg_list` | List for the Flutter subscriber-form group picker. → `{items, count}` |
| GET | `/api/v1/subscriber-groups/{gid}` | `sg_edit` data | → `{group, members}` (members ≤200). 404 if missing. |
| POST | `/api/v1/subscriber-groups` | `sg_create` | Body below. → `201 {group}`. 422 on missing/duplicate name. |
| PATCH | `/api/v1/subscriber-groups/{gid}` | `sg_update` (non-speed path) | Partial update. → `{group}`. 404/422. |
| DELETE | `/api/v1/subscriber-groups/{gid}` | `sg_delete` | Detaches members. → `{id, deleted:true}` (idempotent). |
| POST | `/api/v1/subscriber-groups/{gid}/disconnect-online` | `sg_disconnect_online` | → `{group_id, disconnected, failed, members}`. 404 if missing; 502 if sessions unreadable. |
| POST | `/api/v1/subscriber-groups/{gid}/quota/reset-daily` | `sg_quota_reset_daily` | → `{group_id, reset, failed}`. 404 if missing. |

**Create/Patch body fields** (same as the web form `_form_to_kwargs`):
`name` (required), `description`, `bandwidth_schedule_id` (int|null),
`default_plan_id` (int|null), `default_auto_renewal` (bool),
`working_days` (CSV cache), `connection_schedule` (JSON string).

**Membership/assign:** a subscriber joins a group by its `subscriber_group_id`
/ `group_name` on the subscriber record (set via the subscribers/users API) —
there is no separate web "assign member" action, so none is added here. The
group's current members are returned by the GET-one endpoint.

**Bug fixed while wiring (shared query layer):** `subscriber_groups_repo.list_members`
selected unqualified `id` while joining `subscribers` + `subscriber_groups`
(both have `id`) → `ambiguous column name: id`; the member list never loaded
(web edit page included). Columns are now qualified `s.*`. Pure fix, no
behavior change beyond the list now loading.

Tests: `tests/test_api_subscriber_groups.py` (13).

---

## Group 2 — Sessions speed data + speed filter
Extends `GET /api/v1/sessions/online` to mirror the **speed** columns/filter of
the web online-sessions list (`routes/sessions.py:online_list`). Additive —
existing fields/filters (`type`, `q`) unchanged. File: `app/api/v1/sessions.py`.

**Per-session fields added** (raw speed fields `rate_down_kbps`, `rate_up_kbps`,
`plan_down_kbps`, `plan_up_kbps`, `has_custom_speed`, `has_temporary_speed`
were already present via the `OnlineSession` dataclass):
- `speed_state`: `"temporary"` (active temp window) → `"custom"` (permanent
  override) → `"normal"`. Same precedence as the web row pill.
- `has_active_temporary_speed` (bool), `has_special_speed` (bool).
- `temporary_speed_window`: `{active, unknown, expired, remaining_seconds,
  ends_at, ends_at_epoch, custom_speed}` or `null` — the same window state the
  web countdown uses.

**New query param** `speed` (mirrors web `selected_speed`):
`all`/empty · `special` (custom OR active temp) · `temporary` (active temp
only) · `normal` (no special). Unknown value → `422`.

**Response additions:** `speeds` breakdown `{normal, custom, temporary}` and the
echoed `speed`. Like the web page, the endpoint first runs
`expire_due_temp_speeds` (revert CoA for elapsed windows) so the listing never
shows a throttled session past its window.

**Single source of truth:** the temp-window logic moved to
`services/temp_speed.temp_speed_states(tenant_id, usernames, now)`; the web
route's `_temporary_speed_states` now delegates to it (output byte-identical) so
web + API share one implementation (no duplicated logic, no web behavior
change).

Tests: `tests/test_api_sessions_speed.py` (7). (`test_online_list_separation`'s
4 failures are the pre-existing 403/RBAC ones, unrelated.)

---

## Group 3 — Site-Exit
Mirrors the site-exit web page (`routes/site_exit.py`,
`/admin/radius/site-exit/<nas_id>`). File: `app/api/v1/site_exit.py`. Reuses
`site_exit_policies_repo` / `_deployments_repo` / `_targets_repo` /
`vps_exit_nodes_repo` + `site_exit_script_planner` / `_renderer` / `_presets`.

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/api/v1/site-exit/routers/{nas_id}` | `site_exit_page` / `_render_page` | Page state. `?policy_id=` selects a policy. → `{nas, policies, policy, deployment, targets, group_counts, vps_nodes, presets, group_meta, apply_disabled_reason}`. 404 if router missing. |
| POST | `/api/v1/site-exit/routers/{nas_id}/policies` | `site_exit_policy_create` | Body `{name*, exit_node_id*, fail_mode?, include_subdomains?, include_router_output?}`. → `201 {policy}`. 422 bad name/node, 409 duplicate. |
| GET | `/api/v1/site-exit/routers/{nas_id}/policies/{policy_id}/plan` | `site_exit_preview` | Read-only plan: `{can_apply, forward_script, rollback_script, summary, total_commands, warnings, blocking_errors, targets_skipped}`. `?wan_interface_list=`. No wire. |

`presets` returns metadata only (`key, label_ar, description_ar, target_count`)
— the large raw preset body is omitted.

**Follow-up (deferred, explicit):** live **apply** (forward/rollback crossing
the wire) + **targets-save** + **seed-import**. Apply needs the 5-confirmation
safety gate (`site_exit_safety.evaluate`) and VPS/NAS acceptance, and the web
apply button is itself UI-gated today; the plan endpoint already exposes the
rollback script so the app can display it. These land as a later sub-task.

Tests: `tests/test_api_site_exit.py` (7).

---

## Group 4 — Events / Risk / Security / Investigations
Mirrors `routes/events_risk.py` (`/admin/radius/events*`). File:
`app/api/v1/events.py`. Reuses `EventsRiskCenterService`.

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/api/v1/events` | `events_center` | Filters: `category, severity, actor_type, actor_id, target_type, target_id, correlation_id, from, to`. → `{events, count, summary}`. |
| GET | `/api/v1/events/{event_id}` | `events_detail` | → `{event, timeline}`. 404 if missing. |
| GET | `/api/v1/events/risk` | `events_risk` GET | → `{flags, summary}`. |
| POST | `/api/v1/events/risk/run` | `events_risk` POST | Runs risk rules. → `{result, flags}`. |
| GET | `/api/v1/events/security` | `events_security` | → `{events (category=security), flags (open)}`. |
| GET | `/api/v1/events/investigations` | `events_investigations` GET | `?status=`. → `{investigations}`. |
| POST | `/api/v1/events/investigations` | `events_investigations` POST | Body `{title*, severity?, entity_type?, entity_id?, summary?}`. → `201 {investigation}`. 422 missing title. |

(The `/events/{id}` int route is registered after the literal sub-paths so
`risk`/`security`/`investigations` are never shadowed.)

Tests: `tests/test_api_events.py` (8).

---

## Group 5 — Network Telegram Alerts
Mirrors `routes/network_telegram_settings.py` (`/admin/radius/network/telegram`).
File: `app/api/v1/network_telegram.py`. Reuses `tenant_telegram_settings_repo`
+ `telegram_notifier`.

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/api/v1/network/telegram` | `network_telegram_settings` | → `{settings}`. Token **never** returned raw: `has_bot_token` + `bot_token_masked` + `chat_id, thread_id, enabled, updated_at, ready`. |
| PATCH/PUT | `/api/v1/network/telegram` | `network_telegram_save` | PATCH-style: only body keys change (absent `bot_token` preserved — no accidental secret wipe). → `{settings}`. |
| POST | `/api/v1/network/telegram/test` | `network_telegram_test` | Sends a live test message. → `{sent:true}` or `502 {sent:false}`. |

Secret-safety note: GET masks the token (the web form pre-fills it; the API
does not echo secrets). `ready` mirrors the web's "alerts will work" condition
(`enabled && bot_token && chat_id`).

Tests: `tests/test_api_network_telegram.py` (6).

---

## Group 6 — WhatsApp Auto-Reply Bot
Mirrors `routes/communications.py:communications_bot_settings`
(`/admin/radius/communications/bot`). File: `app/api/v1/whatsapp_bot.py`.
Reuses `comms_bot.load_bot_config` / `save_bot_config` (storage
`tenant_settings comms.bot.*`, no migration). Mounted at `/whatsapp/bot` (the
existing `/whatsapp` endpoints are the notification provider settings).

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/api/v1/whatsapp/bot` | bot settings GET | → `{config:{enabled, greeting, fallback, commands[], active_commands_count}, webhook_url, channel_ready}`. |
| PUT/PATCH | `/api/v1/whatsapp/bot` | bot settings POST | Save. Absent keys preserved. `commands` = rules `[{keyword, reply_template, enabled}]` (empty entries dropped by `save_bot_config`). → `{config, webhook_url}`. |

Tests: `tests/test_api_whatsapp_bot.py` (4).

---

## Group 9 — Bandwidth schedules: `sr_days` + copy-from
Extends the existing `POST /api/v1/bandwidth-schedules` (no new routes) to
match the web speed-rules form (`_speed_rules_panel.html` /
`_payload_from_saved_schedule`). File: `app/api/v1/bandwidth_schedules.py`.

- **`sr_days`** — the web's day-checkbox field. Now accepted as an alias for
  `days_csv` (list `["sat","sun"]` or CSV `"sat,sun"`). Explicit `days_csv`
  still wins. (`days_csv` already round-tripped via the service.)
- **`source_schedule_id`** (copy-from) — copies speed/CIR/time/days/restore
  from a saved schedule into the new one (mirrors `_payload_from_saved_schedule`,
  reuses `operations.get_bandwidth_schedule`); explicit non-empty body fields
  override, `name` defaults to «نسخة من …», `metadata.copied_from_schedule_id`
  is recorded. Missing source → ignored (same as web). List responses already
  include `days_csv`.

Tests: `tests/test_api_bandwidth_schedules_parity.py` (7).

---

## Group 8 — Reports: login-states detail
Mirrors `routes/reports.py` `rep_login_states*` (`_render_login_states_detail`
+ `rep_login_states` overview). File: `app/api/v1/reports_login_states.py`.
Reuses `login_events.fetch_login_events` + `login_states_overview`.

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/api/v1/reports/login-states` | `rep_login_states` | → `{overview, kinds}`. |
| GET | `/api/v1/reports/login-states/{kind}` | `rep_login_states_{kind}` | `kind ∈ subscribers, cards, sub_portal, card_store, admin`. Filters `result, source, q, date_from, date_to`. → `{kind, actor, source_locked, rows, stats, shown, matched}`. 404 unknown kind. |

Each kind pins the **same actor + source-lock** as its web route (e.g.
`subscribers`/`cards` → `source=network`, `sub_portal`/`card_store` →
`source=portal`, `admin` free) so RADIUS and portal events never mix; the
`?source=` query can't override a locked kind.

Tests: `tests/test_api_reports_login_states.py` (6).

---

## Group 7 — MikroTik web-only pages → JSON (sub-series)

### 7a — Device Health ✅
Mirrors `routes/device_health.py` (`/admin/radius/device-health`). File:
`app/api/v1/device_health.py`. Reuses `device_health` service + repo.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/device-health` | `{summary, devices, routers}` (overview). Filters `router_id, status, device_type`. |
| GET | `/api/v1/device-health/devices` | `{devices, summary}`. |
| POST | `/api/v1/device-health/devices` | Create. → `201 {device, warnings, network}`. 422 invalid. |
| PATCH | `/api/v1/device-health/devices/{id}` | Update. → `{device, warnings}`. |
| DELETE | `/api/v1/device-health/devices/{id}` | → `{id, deleted}`. |
| POST | `/api/v1/device-health/devices/{id}/enable`\|`/disable` | Monitoring toggle. |
| GET | `/api/v1/device-health/devices/{id}/events` | Status-change history. |
| GET | `/api/v1/device-health/devices/{id}/alerts` | Alert decisions. |
| POST | `/api/v1/device-health/devices/{id}/test-ping` | Live reachability probe. |
| GET | `/api/v1/device-health/router-interfaces?router_id=` | Router interface list. |
| GET\|POST | `/api/v1/device-health/live-apply` | Panel live-apply toggle (get/set). |

Deferred (group-7 follow-up): bulk `poll` (+SSE stream) and per-device `apply`
(push to router, gated by `HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY`).

Tests: `tests/test_api_device_health.py` (8).

### 7b–7i — REMAINING (not yet built)
Each is a web-only MikroTik page to expose as `/api/v1` JSON, reusing its
existing service (one module + commit each):
- **topology** — `routes/mt_topology.py` (graph/nodes/links state).
- **login-designer** — `routes/mt_login_designer.py` (hotspot login template CRUD/preview).
- **programming** — `routes/mt_programming.py` (object inventory + Q4 cleanup; live ops gated).
- **audit-timeline** — `routes/mt_audit_timeline.py` (per-router change timeline, read-only).
- **recovery-plan / problems** — `routes/mt_recovery_plan.py` + `routes/mt_problems.py`.
- **permission-matrix** — `routes/mt_permission_matrix.py` (read; saves are super-only).
- **metrics / push script-generators** — `routes/mt_*` script generators (read-only generators).
- device-health `poll`/`apply` (from 7a) — live-wire, gated.

See the RESUME NOTE in the session for exact next steps.
