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
