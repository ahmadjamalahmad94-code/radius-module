# SECURITY RC2 — mainline release candidate 2 (local only)

| | |
|---|---|
| Branch | `release/security-rc2` (worktree `C:\Projects\rm-sec-rc2`) — **local only: not pushed, not tagged, not deployed** |
| Start point | `agent/ops-pilot-client20` `0a8fdfa8` = RC1 (`release/security-rc1` `94a79f28`, incl. B-14 local NAS / migration 202) + ops executor + web chat + merge of `agent/round6-base` `9465355c` + central-model client + `/api/v1/ops/assistant/*` |
| Production base for comparison | `agent/round6-base` `9465355c` (local ref, nothing fetched) |
| Date | 2026-10-07 |
| Previous RC | `SECURITY_RC1.md` — everything there still applies unless changed below |

## 1. Contents (merged `--no-ff`, one at a time, in this order)

| # | Branch | Result | Commits |
|---|---|---|---|
| 1 | `agent/round6-base` (local ref `9465355c`) | **Already up to date** — already merged into the start point (`39980cac`). No merge commit needed. | — |
| 2 | `agent/sec-360-secrets` | merge `f6b1d03b`, no conflict | `45e3f996` PPPoE password masked in `GET /api/v1/accounts/<u>/360`; `5c55535f` card passwords masked in `/api/v1/card-users/<id>/360` + purchase response; `7a583bf4` card passwords masked in `POST /api/v1/cards/generate`; `6ff5c77e` doc `SEC_360_SECRETS.md` |
| 3 | `agent/terminology-glossary` | merge `539de08e`; conflict only in `translations/MASTER.csv` | `d8f3f21e` en, `ac8b86ab` fr, `3e2ceb45` tr, `e97ff889` es (one term per concept), `b175baa3` `docs/TERMINOLOGY.md` |
| 4 | `agent/sec-handoff-scrub` | merge `a01b7206`, no conflict | `3de32072` exposed API token value removed from `HANDOFF_2026-05-20.md` (verified: the removed value no longer occurs anywhere in the tree) |

**i18n conflict resolution (no hand edits):** `.po` files merged cleanly; `MASTER.csv` regenerated with
`python tools/i18n_master.py sync && export && compile`, `check` → 0 problems. `messages.pot` changed only in
source locations. Catalog: 24,674 msgid; 14,401 empty cells per locale (en/fr/tr/es).

**Migrations:** no new migration in items 2–4. Prefixes ≥195: 195…202, 210, 211 — no duplicates.
`201_sec_b14_radius_source_tenant`, `202_sec_b14_radius_local_nas`, `210_ops_assistant`, `211_ops_messages` keep
their names (already applied on client20). Only legacy duplicates remain (027, 085, 164 — on main and applied
under both names everywhere; tolerated by `test_tunnels_page_and_dup_migration`). Nothing renumbered in RC2.

## 2. Test evidence

All local, SQLite in temp dirs, **each test file in its own pytest process**. RC tree = `a01b7206`; base = detached `9465355c`.

| Tree | Files | Passed | Failed |
|---|---|---|---|
| **RC2** `release/security-rc2` | 172 | **1759** | **0** |
| base `agent/round6-base` (files that exist there) | 142 | 1301 | 0 |

* The 30 extra files on RC2 are the security / ops / product tests absent from the base.
* **Every one of the 142 common files has the same pass count on both trees → 0 new failures, 0 pre-existing failures.**
* Groups on RC2: all 26 `tests/test_sec_*.py` **243 passed** (incl. `test_sec_360_secrets` 3, `test_sec_card_user_360_passwords` 7); ops (`test_ops_*`, 8 files) **224 passed**; `test_hrai_scope_tenant_guard` 15; i18n (7 files incl. `test_i18n_no_leak_guard` 6/6) **115 passed**; migration tests (19 files) **231 passed**; API contracts / permission-guard coverage (`test_api_*contracts*`, `test_api_permission_guard` 22, `test_perm_guard_coverage` 5, `test_qa_permission_guard`, `test_business_os_*contracts`) all pass; `test_api_card_offers` 8.
* Broad related subset: file names matching card_user|cards_|generate|accounts|admins|auth|login|tenant|nas|store|mt_dashboard|seed|log_retention|license_admin|openapi|secret|rbac|freeradius|ops, plus every test file changed vs the base.
* `test_ops_executor_validation`: the sweep's last line was a log line; re-run alone: 37 passed.
* Not run here: `radiusd -XC` (no FreeRADIUS binary) — mandatory on the candidate image (RC1 §4 step 5).

## 3. Migrations and production boot (RC2 tree)

* Fresh SQLite, `run_pending_migrations()` twice: **199 applied, 0 on re-run**; `_migrations` contains 195–202, 210, 211 under the names above; B-14 objects present.
* `create_app()` with `HOBERADIUS_ENV=production`, fresh DB:
  * random 64-hex `FLASK_SECRET` → **boots**;
  * `change-me-to-32-random-bytes-please` (deploy/.env.example), `replace-with-a-long-random-flask-secret` (.env.example), `dev-secret-change-me` → **refused (`RuntimeError`)**.

## 4. Deploy order (per server; each step an explicit owner go/no-go)

0. Read-only audit right before deploy (RC1 §4 step 1–2: HEAD, env names, tokens, admins on known passwords, `tools/sec_b14_detect.sql`).
1. **Licence panel B-22 first** — `radius-module-admin` `agent/sec-fix-b22` (panel signs runtime/capacity contracts, only the platform owner edits owner designations). Then RC2 radius refuses unsigned designations. Reverse order is fail-safe but stalls designations.
2. Build ONE image from RC2; run `radiusd -XC` inside it with the new `mods-enabled/sql` + `rest`.
3. Backup DB.
4. **App before FreeRADIUS**: start the app (applies 201/202/210/211 if missing) → confirm `_migrations` lists them → **only then** restart freeradius. Never the other way, never both at once (the new SQL reads `radius_source_tenant` / `radius_local_nas`).
5. **B-14 local NAS registration** (multi-tenant servers only: client1, client20; after 202 applied): register every source the host itself sends RADIUS from, e.g. client20's accel gateway
   `docker exec hoberadius python /app/tools/radius_local_nas.py --db /app/instance/hoberadius.db set 10.50.0.1 --purpose mgmt --service accel-ppp`
   (`--purpose nas --tenant N` if it serves one tenant's data connections; `probe` only for health probes). Loopback stays unregistered = fail closed. Re-run D6/D7: `UNKNOWN` must be empty. Single-tenant servers: nothing to do. Owner first decides whether client20's empty tenant 2 (`acme`) stays (see `SEC_B14_LOCAL_NAS.md` §5).
6. **`HOBERADIUS_ENV` decision**: target `production` (today unset everywhere). Effects: weak/template `FLASK_SECRET` refused at boot (audit: all current secrets OK), no default seed passwords, no API dev-token fallback, `Secure` session cookie by default. **Panels on plain HTTP must also set `HOBERADIUS_SESSION_COOKIE_SECURE=0`** or login breaks. Leaving it unset keeps today's behaviour (the rest of RC2 still applies).
7. Smoke: owner + tenant-manager web login, MikroTik dashboard, one RADIUS Access-Accept, `radius_unattributed` empty on single-tenant servers, no `owner_admins designation … REFUSED` in logs; API 360 shows `••••••` for `pppoe_password` / card passwords.
8. **Ops assistant (optional, pilot client20 only, off by default)** — enable only after 1–7 and tenant isolation verified. Panel env:

   | Variable | Default | Note |
   |---|---|---|
   | `HOBERADIUS_OPS_MODEL_URL` | `http://127.0.0.1:8095` | model server / central gateway; in Docker 127.0.0.1 is the container — use a reachable non-public address |
   | `HOBERADIUS_OPS_MODEL_KEY` | — | central gateway key (`hrops_…`), env/secret only |
   | `HOBERADIUS_OPS_MODEL_TIMEOUT` | `45` | per call; 55 s budget per admin message |
   | `HOBERADIUS_OPS_MODEL_CONCURRENCY` | `2` | per panel process |
   | `HOBERADIUS_OPS_MODEL_CA` / `_CLIENT_CERT` / `_CLIENT_KEY` | — | https / mTLS to the central server |
   | `HOBERADIUS_OPS_PROMPT` | `v3` | **the round-2 2B model needs `HOBERADIUS_OPS_PROMPT=v1`** (SPEC_DATA_v1 prompt + `{n, policy, label_ar}` policy items); `v3` only for ops-v2-trained adapters |

   Then `POST /api/v1/ops/flag {"enabled": true}` (owner). The page appears only when every admin of the tenant has a non-default, non-temporary password. Details: `docs/OPS_EXECUTOR.md`.

Mobile app note: `/360`, purchase and `cards/generate` now return masked passwords (`••••••`, `has_pppoe_password`); the Flutter app does not display them (`SEC_360_SECRETS.md`).

## 5. Rollback

* Code: redeploy the image each server ran before (its HEAD recorded in step 0 — e.g. `825c7b45`/`9465355c` round6-base on Abed/Barq/Fadi, the ops-pilot build on client20 which already has 201/202/210/211, `e9d652ee` on client1/spare-62) with `deploy.sh upgrade` (rebuild), together with the **old** `mods-enabled/sql`/`rest`.
* Schema: 201/202/210/211 are additive (views/tables) — leave them; old code ignores them. If the B-14 backfill was applied, undo it first with its undo file. Full 202 removal steps: `SEC_B14_LOCAL_NAS.md` §6.
* No-code switches: `HOBERADIUS_ENV` unset (F-3/F-5 boot problems), `HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT=1` / `…_SERVER_WIDE=1` (B-07), ops assistant `POST /api/v1/ops/flag {"enabled": false}` or unset the model URL.
* Single fixes: revert the merge commit (`git revert -m 1 f6b1d03b` sec-360; `539de08e` terminology — then `i18n_master.py sync && export && compile`; `a01b7206` doc only). B-22: stored designations untouched; panel signing is backward compatible.
* Per-fix details: `SECURITY_RC1.md`, `SEC_360_SECRETS.md`, `SEC_B14_LOCAL_NAS.md`, `SEC_FIX_B14.md`, `SEC_B22_RADIUS.md`, `docs/OPS_EXECUTOR.md`, `docs/TERMINOLOGY.md`.
