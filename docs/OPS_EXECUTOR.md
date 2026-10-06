# Operations assistant — deterministic executor (ops-v1)

Owner decisions: `hoberadius-ai-support/ops/DECISIONS.md` (4 levels, level 5 out of scope).
Contract the model is trained on: `ops/SPEC_DATA_v1.md` + `ops/catalog/actions.json`
(copied verbatim to `app/radius/services/ops_assistant/catalog_ops_v1.json`).

The model (an LLM outside this app) only converses and emits ONE JSON proposal per turn.
Everything that touches data is this executor: deterministic code, the logged-in admin's
own permissions, the real `/api/v1` handlers, an audit row for every decision.

## Architecture

```
model ──proposal──▶ /api/v1/ops/...  (app/api/v1/ops.py)
                         │
                         ├─ gate.py        flag ops_assistant.enabled (default OFF) + password gate
                         ├─ context.py     CONTEXT (today_local Asia/Gaza, currency, role, permissions)
                         │                 CHOICES via the real list endpoints → ids issued per conversation
                         ├─ validator.py   forbidden keys (deep) → JSON schema → issued ids + scope
                         │                 → units/caps → permission (same decision as the API guard)
                         ├─ executor.py    L1 draft · L2 confirm+execute · L3 plan step by step
                         ├─ dispatch.py    nested request through the REAL /api/v1 stack, same credential
                         ├─ detectors.py   L4 events (detection only)
                         ├─ store.py       ops_conversations / ops_issued_choices / ops_proposals (mig. 210)
                         └─ audit.py       audit_log rows target_type='ops_assistant'
```

**Why `dispatch.py`.** Writes are never re-implemented. Each action is a nested request
(`app.test_request_context(...)` + `app.full_dispatch_request()`) to the same endpoint the
mobile app calls (`POST /accounts`, `/accounts/<u>/extend`, `/change-plan`, `/disable`,
`/enable`, `/sessions/temp-speed`, `/accounts/<u>/temp-speed/cancel`, `/profiles`,
`/cards/offers`, `/cards/generate`), carrying the SAME credential as the outer request. So
authentication, `permission_guard`, distributor/subscriber scoping, manager credit caps,
license capacity, idempotency, audit/manager-activity rows, notifications and CoA/PoD side
effects are identical to an app call. The tenant always comes from that credential.
`flask.g` is snapshotted/cleared/restored around the inner request (Flask 3 shares one
app context).

**Unbound credentials are refused.** An env token or a DB token without `created_by`
bypasses RBAC, so every `/ops/*` endpoint returns 403 `ops_requires_admin` for it.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/ops/status` | `{available, flag_enabled, reason, password_gate{ready, admins_checked, default_password_count, must_change_count}, catalog_version}` — counts only |
| POST | `/api/v1/ops/flag` | `{enabled: bool}` — owner / co-owner only (`SUPER`) |
| GET | `/api/v1/ops/events` | level-4 events for this admin (scoped) |
| POST | `/api/v1/ops/conversations` | `{event_type?}` → `{conversation_id, context, context_message}` (`"CONTEXT {...}"`) |
| GET | `/api/v1/ops/conversations/<cid>/context` | fresh CONTEXT |
| POST | `/api/v1/ops/conversations/<cid>/choices` | `{source: list_plans\|list_offers\|find_subscriber\|change_plan_policies, query?, status?, username?, plan_id?}` → `{choices, tool_message}` (`"CHOICES {...}"`) |
| POST | `/api/v1/ops/conversations/<cid>/proposals` | `{proposal, mode: draft\|execute}` |
| POST | `/api/v1/ops/conversations/<cid>/confirm` | `{proposal_id, proposal_hash}` + optional `Idempotency-Key` header |
| GET | `/api/v1/cards/offers` | NEW (catalog Q2) — owner all (`include_inactive=1` owner only), manager only offers shared with him; wholesale/margin hidden without `can_see_wholesale`/`can_see_profit` |
| POST | `/api/v1/cards/offers` | NEW (catalog Q2) — owner or the `offer/create` entity grant; `CardOffersService.create_offer` (plan required in tenant, duration > 0, selling ≥ wholesale) |

A conversation belongs to ONE tenant and ONE admin: any other tenant/admin gets 404.

### `proposals` responses
* control (`ask/choose/refuse/cancel`) → `{kind:"control"}`; a `choose` with a list source
  runs the list and returns `choices` + `tool_message`.
* lookup (`list_plans/list_offers/find_subscriber`) → `{kind:"lookup", choices, tool_message}`.
* `mode=draft` (level 1) → `{level:1, draft:[{api{method,path}, payload, password, pending_refs, display}]}` — nothing written; confirming a draft → 409 `draft_only`.
* `mode=execute` (level 2/3) → `{level, proposal_id, proposal_hash, requires_confirmation, confirmation:[card per step], not_executable_steps}`. The card is built by the executor from validated values (never from `summary_ar`).
* rejected → 422 `proposal_rejected` (or 403 `proposal_forbidden` when only permission/scope failed) with `details.violations[{code, message, path}]`. Values are never echoed.

### `confirm`
* hash must equal the stored hash (constant-time) else 409 `confirmation_mismatch` (audited). The hash covers conversation + action + fields/steps — not `summary_ar`; any change → new proposal → new hash → new confirmation.
* the proposal is RE-VALIDATED (permission, scope, caps may have changed).
* `pending → executing` is claimed atomically; a second confirm replays the stored report (`replayed: true`), never executes twice. A header `Idempotency-Key` already used by another proposal → 422 `idempotency_key_reused`.
* the key is forwarded to the money/batch endpoints that honour it (`extend`, `change-plan`, `cards/generate`; plan steps use `<key>:<n>`).
* response: `{report{status: executed|failed|partial, steps[{n, action, status: done|failed|not_run, result, error}]}, model_result: "RESULT {...}", show_once?}`.

## Validation (in order)
1. object; **forbidden key anywhere → whole proposal rejected** (`forbidden_model_fields` + secret-like names: `*_password`, `*_secret`, `*_token`, `pin`, `api_key`…; `login_without_password` / `password_length` / `password_generation_type` stay allowed).
2. JSON schema: catalog `output_schema` (dependency-free checker `schema_check.py`, fails closed on unknown keywords; cross-checked against `jsonschema` in tests). Level 3: `PLAN_SCHEMA` (1–6 executable steps) + each step's action schema; `$stepN.field` only into `plan_id`/`offer_id`/`username`, N earlier, field produced by that step.
3. ids: every `plan_id`/`offer_id`/existing `username` must have been issued in a CHOICES list (or the level-4 event) of THIS conversation (`invented_id`); an existing subscriber must pass `subscriber_in_scope` (`out_of_scope`). Step results are issued after they succeed.
4. units/caps: Arabic-Indic digits → Latin; `*_local` (Asia/Gaza, DST) → UTC; durations → minutes, **months = calendar months in the tenant zone** (12 months over a leap day = 366 days → refused); **one operation ≤ min(365 days, server limit)** (`over_one_year`); extend `until` must be after max(now, current expiry); temp speed 1–1440 min, each direction 0 or 64–1,000,000 kbps, not both 0; speed kbps / quota MB with 1024 (card shows "2 Mbps", "5 GB"); selling ≥ wholesale; card count ≤ batch caps; prefix+suffix shorter than the total length; change-plan policy must match the per-minute direction.
5. permission: same `permission_guard.decide` as the API (+ in-handler checks: `offer/create` grant, direct-generation grant).

## Level-specific notes
* **create_subscriber**: the password is generated server-side (`secrets`, 10 chars) unless `login_without_password`; returned once in `show_once`, never stored, logged, audited or put in `model_result`. Duration → `expire_at` set directly (Q5 executor default); no duration → the server's `create_without_expiry` rule (shown in the card).
* **create_card_batch** from a plan: card codes in the API response are dropped by a per-action whitelist. From an offer: web-only in v1 (Q2/Q7) → draft/hand-off, confirm reports `not_executable_v1`.
* **temporary_speed apply**: the executor picks the live `session_id` from `/sessions/online`; offline → `not_online` (Q4).
* **Level 3**: one confirmation; stop at the first failure; later steps `not_run`; completed steps stay done (no rollback) and the report says exactly what happened.
* **Level 4 detectors**: `expiring_tomorrow` (local tomorrow, scoped to the admin), `repeated_rejects` per NAS (full-access admins only; never reads `radpostauth.pass`; settings `ops_assistant.rejects_window_minutes`=15, `ops_assistant.rejects_threshold`=20), `low_card_stock` (unused cards per plan in visible batches < `ops_assistant.low_card_threshold`=20), `plan_without_offers` (owner). Detection only; a conversation started with `event_type` gets the event in CONTEXT and its ids issued — any suggestion still needs the normal confirmation.

## Availability
* tenant setting `ops_assistant.enabled` — default `0` (OFF); owner toggles via `/ops/flag`.
* owner rule: unavailable while ANY admin who can act on the tenant (active member, raw `is_super_admin`, or — tenant 1 — no membership) is enabled and has `must_change_password` or a password that verifies against public defaults (seed values, common defaults, the username). Verdicts are cached per (admin, sha256(hash)), so a password change takes effect immediately. Results are counts only.

## Audit
`audit_log` rows `action='ops.<event>'`, `target_type='ops_assistant'`:
`conversation`, `choices`, `validate` (validated/rejected/control), `draft`, `confirm`
(confirmed/mismatch/rejected), `execute` (executed/failed/partial + per-step results),
`replay`, `flag`. Payload: conversation id, admin id/login, proposal id + hash,
`confirmed_at`, violations or step results — never secrets. The inner API calls keep
writing their own audit rows as for any app call.

## Tests
`tests/test_ops_executor_units.py`, `_validation.py`, `_flows.py`, `_plans.py`,
`_gate_events.py`, `tests/test_api_card_offers.py` (helpers: `tests/ops_exec_helpers.py`),
`tests/test_ops_assistant_web.py` (web chat + model loop against a fake local
OpenAI-compatible server).

## Web chat & model wiring

```
browser (session + CSRF) ─▶ /admin/radius/ops-assistant/*   (routes/ops_assistant.py)
                                │  web_bridge.Api: in-memory, admin+tenant-bound `login:ui-ops:` token
                                ▼
                         conversation.py ──▶ model_client.chat ──▶ llama-server (OpenAI API)
                                │  ◀── ONE JSON object (parse_proposal; anything else = error, nothing runs)
                                ▼
                         /api/v1/ops/* (this executor, same RBAC as the app) — validate / CHOICES / confirm
```

| Method | Path (`/admin/radius` prefix) | Notes |
|---|---|---|
| GET | `/ops-assistant` | page; when unavailable shows WHY (flag off / counts of admins with default or temporary passwords) |
| POST | `/ops-assistant/message` | `{conversation_id?, text}` → `{conversation_id, replies[]}` |
| POST | `/ops-assistant/confirm` | `{conversation_id, proposal_id, proposal_hash}` → `{report, show_once?}` (`Cache-Control: no-store`) |
| POST | `/ops-assistant/cancel` | `{conversation_id}` — records «إلغاء» in the transcript, no model call |
| GET | `/ops-assistant/events` | level-4 suggestions (one row per event record) |
| POST | `/ops-assistant/start-event` | `{event_type, index}` → new conversation from that record + first model turn |

* Guards: `login_required` + the blueprint login guard; global CSRF (`X-CSRFToken`) on every
  POST; `gate.availability` on every endpoint (403 `unavailable` JSON); the routes are in
  `_GUARD_ALLOWLIST` because each action is decided by the executor for THIS admin.
  Sidebar entry «مساعد العمليّات» only when the flag is on AND the password gate is open
  (`ops_assistant_nav_visible`, cached 30 s per tenant and process).
* `replies[]` items: `assistant` (summary_ar of ask/refuse/cancel), `choices` (the list shown
  to the admin — the same items the model sees), `proposal` (the executor's card: steps,
  values, display, danger, password note, `$step` refs, not-executable steps; plan/offer
  names added for display), `error` (`model_unavailable`, `invalid_model_output`,
  `proposal_rejected|proposal_forbidden` with the executor's violation messages,
  `too_many_hops`).
* **Loop** (`conversation.run_model`): model → parse ONE object → `POST …/proposals`
  (executor validation, never re-coded). `choose` / `list_*` → CHOICES appended as a `tool`
  message → model again, **max 3 CHOICES hops per admin message**. `choose
  change_plan_policies` takes `username` + `plan_id` from the latest assistant turn that
  named both. Executable action / plan → confirmation card; **execution only via
  `/confirm`** (the admin's click carrying the executor's id + hash).
* **Transcript** (`ops_messages`, migration 211): CONTEXT (system), CHOICES / RESULT (tool),
  user and assistant turns (`json.dumps(obj, ensure_ascii=False)`, as in training). The
  SYSTEM_PROMPT is prepended at call time. Invalid model output is NOT stored. RESULT is the
  executor's redacted `model_result`; `show_once` goes to the browser only (modal with a
  copy button, removed from the DOM on close) — never to the transcript, the model or logs.
* **Rendering** = `ops/chat.py` verbatim (`model_client.normalize_messages`): SYSTEM_PROMPT +
  CONTEXT merged into ONE system message (blank line); if the first non-system message is
  not the admin's, the fixed `EVENT_TRIGGER` user turn is inserted (level-4 conversations).
  CHOICES items and level-4 events are rendered with the SPEC_DATA_v2 §4/§5 key sets
  (`list_plans {n,id,name,price,currency,duration_value,duration_unit,plan_type}`,
  `find_subscriber {n,username,full_name,plan,status,expires_local}`,
  `change_plan_policies {n,policy,label_ar}`, event → `CONTEXT.event{type,data}` +
  `CHOICES {"source":"event"}`) — ids unchanged, so the executor's issued set still holds.
* Model request: `POST {URL}/v1/chat/completions`, `temperature 0`, `max_tokens 512`,
  `stream false`, `chat_template_kwargs {"enable_thinking": false}`.
* Invalid output / rejection / hop limit → `audit_log` `action='ops.model'` (reason only,
  never the text) + a warning log line.

## Deploy & serve the model

The model runs on the SAME host as the panel, bound to loopback only.

Files (from `hoberadius-ai-support`): the base model `Q4_K_M` GGUF and the ops LoRA adapter
converted to GGUF (`convert_lora_to_gguf.py` of the same llama.cpp build), e.g.
`/opt/hoberadius-ops/base-Q4_K_M.gguf` and `/opt/hoberadius-ops/ops-adapter.gguf`.
llama.cpp **b11388** (`llama-server`), CPU build.

`/etc/systemd/system/hoberadius-ops-model.service`:

```ini
[Unit]
Description=HobeRadius operations assistant model (llama-server, loopback only)
After=network.target

[Service]
Type=simple
User=hoberadius-ops
ExecStart=/opt/llama.cpp-b11388/bin/llama-server \
  -m /opt/hoberadius-ops/base-Q4_K_M.gguf \
  --lora /opt/hoberadius-ops/ops-adapter.gguf \
  --host 127.0.0.1 --port 8095 \
  -t 4 -c 4096 --parallel 1 \
  --cache-ram 0 \
  --jinja \
  --temp 0 -n 512
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
MemoryMax=6G

[Install]
WantedBy=multi-user.target
```

* `-t`: physical cores you can spare (the panel, FreeRADIUS and the DB share the host) —
  start with half of them.
* `-c 4096`: SYSTEM_PROMPT + CONTEXT + a few CHOICES lists + history fit; very long
  conversations should be restarted («محادثة جديدة»).
* `--cache-ram 0` is **mandatory** (prompt-cache RAM growth on b11388; see the
  hoberadius-ai-support notes).
* `--jinja` is required: the GGUF chat template renders the merged system message, the
  `tool` turns and `enable_thinking=false` exactly as in training.
* Never bind to `0.0.0.0`; the panel is the only client.

```bash
systemctl daemon-reload && systemctl enable --now hoberadius-ops-model
curl -s http://127.0.0.1:8095/health            # {"status":"ok"}
```

Panel environment (container / service env):

| Variable | Default | Meaning |
|---|---|---|
| `HOBERADIUS_OPS_MODEL_URL` | `http://127.0.0.1:8095` | llama-server base URL (env wins over the tenant-1 setting `ops_assistant.model_url`; http/https only) |
| `HOBERADIUS_OPS_MODEL_TIMEOUT` | `60` | seconds per model call (1–600); one admin message may make up to 4 calls |

When the panel runs in Docker, `127.0.0.1` is the container itself: put the URL on an address
the container can reach (`network_mode: host`, or `host.docker.internal` with the
`host-gateway` extra host and llama-server bound to the docker bridge IP) — never on the
public interface.

Migration `211_ops_messages.sql` runs automatically at boot.

Enable for a tenant (owner): `POST /api/v1/ops/flag {"enabled": true}` (tenant setting
`ops_assistant.enabled=1`). The page and the menu entry appear only when every admin of
that tenant has a non-default, non-temporary password.

## Not done here (next)
* Flutter screens (chat, show_once dialog, step report) — the web panel is done (above).
* API for offer-based generation (`/cards/offers/<id>/generate` with wallet charge) and
  offline temporary speed (catalog Q4); `list_managers` source for offer visibility.
* Detector payloads poorer than SPEC_DATA_v2 §5 (expiring_tomorrow: no balance /
  renewal_price / last_renewal; repeated_rejects: no reason / affected subscribers;
  low_card_stock is per PLAN while the model was trained per OFFER) — until enriched the
  model asks for the missing terms (any proposal is still validated + confirmed).
* Enabling on a customer server only after the security release is deployed there and tenant
  isolation verified (first pilot: client20).
