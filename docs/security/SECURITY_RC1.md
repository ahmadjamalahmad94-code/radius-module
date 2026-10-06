# SECURITY RC1 — mainline release candidate (local only)

| | |
|---|---|
| Branch | `release/security-rc1` (worktree `C:\Projects\rm-sec-rc1`) — **local only: not pushed, not tagged, not deployed** |
| RC base | **`825c7b45`** = `agent/round6-base`, deployed on Abed, Barq (client2), Fadi (client4) 2026-10-06 07:08. Merged in (`c2be8d91`), so **the diff from production to this RC is only the security work** (+ the two product fixes in §1.4). |
| Security base | `agent/sec-merge-sim` `80cdf8d8` (= round6-base `611fc731` + `b38258a4` + B-04/06/07/08/13 + B-14 + B-22 radius + F-1 + F-3 + F-5 + interaction fixes) |
| Date | 2026-10-06 |
| Hosting line | separate RC: `docs/security/SECURITY_RC1_HOSTING.md` on `release/security-rc1-hosting` |
| Licence panel | `radius-module-admin` branch `agent/sec-fix-b22` (§5) |

## 0. Production facts (read-only audit, 2026-10-06)

| Server | Code | Notes |
|---|---|---|
| Abed, Barq (client2), Fadi (client4), and all other mainline servers except below | `agent/round6-base` @ `825c7b45` | = this RC's base |
| client1, spare-62 | **detached HEAD `e9d652ee` (2026-09-12)** | older than the RC base: deploying the RC there also brings 3+ weeks of round6 product changes — treat as a full upgrade, not a security-only patch |
| hosting VM 200 | **STOPPED — not audited** | hosting RC only; re-audit before any deploy |

* client1 and client20 are **multi-tenant (2 tenants each)** → B-14 detection/backfill and the X-Tenant (c) rules matter there; everyone else single-tenant.
* **No active unbound API tokens anywhere** → B-07 / decision (b) break no current integration.
* **`HOBERADIUS_ENV` is unset everywhere** → F-3/F-5 production behaviour and `Secure` cookies are currently OFF on every server (see §4).
* **FLASK_SECRET is OK everywhere** (no template/placeholder value) → turning on production mode will not trip F-3.

## 1. Contents (branch → commits → bypass ID)

### 1.1 Security base (`agent/sec-merge-sim`, merged as-is, history kept)

| Bypass | Branch | Commits (test → fix → docs) |
|---|---|---|
| X-Tenant membership + read-only token scopes (owner decisions a/b, first half of c) | `agent/sec-integration` | `b38258a4` |
| B-04 store key checked against the handler's tenant, never `X-Tenant` | `agent/sec-fixes-app` | `90e772b7` |
| B-06 raw `is_super_admin` no longer logs into the first tenant | `agent/sec-fixes-app` | `cf8730f2` |
| B-07 unbound DB tokens cannot mint tokens / reach server-wide APIs | `agent/sec-fixes-app` | `11a90821` |
| B-08 `/internal/_diag` requires the internal secret | `agent/sec-fixes-app` | `bda0c5db` |
| B-13 admin list/get/edit/delete scoped to the caller's tenant | `agent/sec-fixes-app` | `c9877a11`, docs `a58b8138` |
| B-14 RADIUS accounting / login attempts attributed to the NAS's tenant (migration 201, detection SQL, dry-run backfill) | `agent/sec-fix-b14` | `eed515ab` → `993860ec` → `153b615d` |
| B-22 (radius side) refuse unsigned/badly signed runtime-contract `owner_admins` | `agent/sec-fix-b22-radius` | `159918f8` → `bcd27845` → `5c5f74c2` |
| F-1 no env API token rendered into MikroTik dashboard/operations pages | `agent/sec-fix-f1-mtdash` | `3980e2eb` → `853bcbce` → `46c55f6c` |
| F-3 any value from a shipped env template is a weak `FLASK_SECRET` | `agent/sec-fix-f3-secret` | `6662d9ed` → `e302edf9` → `2aacf3f6` |
| F-5 production seed creates no admin/admin, operator/operator, 123456789 | `agent/sec-fix-f5-seed` | `efafdbda` → `03af0b79`, `3493c0bc` → `991be106` |
| interactions (F-5×F-3 one weak-secret rule; CRLF restore; fresh-prod-boot test; merge doc) | `agent/sec-merge-sim` | `154e380b`, `64a15b5b`, `a54c0bb4`, `80cdf8d8` |

### 1.2 Owner X-Tenant decisions (`agent/sec-xtenant-decisions`, merge `41f08da9`)

| Decision | Status found on sec-merge-sim | Commits |
|---|---|---|
| (a) read-scope tokens cannot POST/PUT/PATCH/DELETE | already enforced (`token_scope_denial`, b38258a4) — evidence tests added | `8571fc1d` (tests) |
| (b) unbound tokens need explicit scopes | already enforced (b38258a4 + B-07) — evidence tests added | `8571fc1d` |
| (c) Basic member selects only HIS OWN ACTIVE tenants; raw flag / header never bypass membership; only owner/co-owners roam | **partial → fixed**: raw flag without membership landed on tenant 1; suspended/closed tenants selectable; foreign header silently re-routed | `8571fc1d` test (12 fail before) → `8072e6ac` fix → `c7278aa4` doc `SEC_XTENANT_DECISIONS.md` |

### 1.3 Production base

| What | Merge |
|---|---|
| `agent/round6-base` `825c7b45` (card edit identity, restamp on batch change, …) | `c2be8d91` — conflicts only in `translations/MASTER.csv` / `messages.pot`, resolved by regenerating them (`tools/i18n_master.py sync && export`) |

### 1.4 Product fixes (`agent/product-findings`, merge `f56eff3e`) — INCLUDED

`de577f16` F-01 MikroTik dashboard «قطع» posts to the real session-disconnect endpoint; `1973e6e0` F-04 invoice-status API tests. Included because it is cleanly separated: same base `611fc731`, touches only `app/static/js/mt_dashboard.js`, `translations/js_msgids.json` and two new test files; no conflict, and its tests pass on the RC together with F-1 (`test_sec_f1_mt_dashboard_token` 9/9, `test_mt_dashboard_disconnect_route` 6/6, `test_api_invoice_status` 15/15).

### 1.5 Temporary-password API (`agent/sec-temp-password-api`, merge `17fd3ed1`) — INCLUDED

`eaa0d4ae` test → `16ea2887` fix (temp/one-time password must be changed before API use, server-side) → `0de521a1` doc `SEC_TEMP_PASSWORD_API.md`. One interaction with decision (c), fixed in **`0f70a133`**: a Basic caller still on a temporary password who names a tenant he may not select now gets the actionable `403 PASSWORD_CHANGE_REQUIRED` instead of `403 tenant_not_selectable` (both refusals; no tenant resolved; wrong password stays 401). The F-5 API xfail recorded by the merge simulation is now closed (test passes).

> Mobile app: the temp-password change alters the app login contract (restricted credential + `must_change_password`). See `SEC_TEMP_PASSWORD_API.md` before shipping.

## 2. Test evidence

All runs local, SQLite in temp dirs, **each test file in its own pytest process** (per-file isolation). RC tree = `0f70a133` (+ this doc); base = detached `825c7b45`.

| Tree | Files | Passed | Failed |
|---|---|---|---|
| **RC** `release/security-rc1` | 194 | **2097** | **0** |
| base `825c7b45` (files that exist there) | 177 | 1895 | 0 |

* The 194 files = all 23 `tests/test_sec_*.py` + `test_hrai_scope_tenant_guard.py` (**234 passed**) and a broad related subset (names matching auth|token|tenant|login|permission|guard|rbac|owner|mt_dashboard|card_edit|restamp|invoice|seed|secret|internal|diag|runtime|license|freeradius|radacct|accounting|admins|session|temp_speed|i18n_no_leak|migration, plus `test_stress_security_fixes`, the round6 card tests and the product-findings tests).
* The 17 extra files on the RC are the security and product tests that do not exist on the base. **No file passes on base and fails on the RC → 0 new failures.**
* `test_migration_real_sources` hit the 25-min per-file limit in the parallel sweep on the RC (machine load); re-run alone: **12 passed** (base 12 passed). Counted above.
* Known pre-existing failures `test_i18n_no_leak_guard` (2, temp_speed/admin_alerts) are **fixed by round6-base `825c7b45`**: 6/6 pass on both trees.
* Key security files on the RC: `test_sec_xtenant_decisions` 38, `test_hrai_scope_tenant_guard` 15, `test_sec_temp_password_api` 14, `test_sec_b06_login_tenant_pick` 7, `test_sec_b07_unbound_tokens` 13, `test_sec_merge_interactions` 3 (the former F-5 API xfail now passes), `test_sec_f1_mt_dashboard_token` 9, `test_sec_f5_seed_defaults` 11.
* Licence panel `agent/sec-fix-b22`: `tests/test_sec_b22_owner_admins.py` **16 passed** (`C:\Projectsma-sec-b22`).

## 3. Migrations and boot (RC tree)

* Fresh SQLite, `run_pending_migrations()` twice: **191 applied, 0 on re-run**; `radius_source_tenant`, `radius_sole_tenant` views and `radius_unattributed` table present (B-14 migration 201).
* `create_app()` with `HOBERADIUS_ENV=production`, fresh DB:
  * random 64-hex `FLASK_SECRET` → **boots**;
  * `change-me-to-32-random-bytes-please`, `replace-with-a-long-random-flask-secret`, `dev-secret-change-me` → **refused (`RuntimeError`)**.
* Not possible here: `radiusd -XC` (no FreeRADIUS binary on this machine) — **mandatory on the candidate image** (§4 step 5).

## 4. Owner's mandatory production sequence

Nothing below is automatic. Each step is a separate explicit decision; stop at the first surprise.

1. **Read-only audit** (per server, no writes): `git rev-parse HEAD`, `git status --porcelain`, env names (not values), tenants count, `api_tokens` rows (`created_by`, `scopes_json`, `revoked_at`, `expires_at`), admins on known passwords, `tools/sec_b14_detect.sql` (`sqlite3 -readonly`). Done 2026-10-06 for all but hosting VM 200 (§0) — **repeat right before deploy**.
2. **Classify tokens / integrations**: every DB token → bound / unbound, scope (full / write / read / none); HobeHub and other integrations → which credential (env token, DB token, Basic). After this RC: read tokens cannot write; unbound tokens without explicit scope get 403 (today: none exist); unbound tokens cannot mint or reach server-wide APIs; Basic `X-Tenant-Id` naming a non-selectable tenant gets 403; temp-password admins get only `/api/admin/me|password|logout`.
3. **Credential decisions**: rotate the env API token that F-1 says was rendered into page HTML (`HOBERADIUS_API_TOKENS`) and update HobeHub; admins still on `123456789` / `admin` (F-5 does not touch existing accounts) → force change; decide opt-ins (`HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT`, `…_SERVER_WIDE`) — default **off**.
4. **FLASK_SECRET / env readiness**: strong `FLASK_SECRET` (audit says OK everywhere), `HOBERADIUS_INTERNAL_SECRET` set (B-08 + RADIUS internal API), then `HOBERADIUS_ENV=production` (see §6 cookie rules). Never rotate FLASK_SECRET casually: it logs everyone out and makes at-rest values encrypted with the old key unreadable.
5. **B-14 detection + `radiusd -XC` on the actual candidate image**: build ONE image from this RC; inside it run `radiusd -XC` with the new `mods-enabled/sql` + `rest`; run the detection SQL (D1 tenants count, D6 unclaimed NAS sources) on each multi-tenant server (client1, client20).
6. **Final release verification**: image digest = RC commit; test evidence of §2 re-run on the exact commit; rollback image identified and available on each server; DB backup taken.
7. **Explicit deploy** — per server, owner go/no-go. Order on each server: backup DB → start **app** (applies migration 201) → confirm `_migrations` has `201_sec_b14_radius_source_tenant.sql` → **then** restart freeradius → smoke (owner + tenant-manager web login, MikroTik dashboard counters, one RADIUS Access-Accept from a known router, `radius_unattributed` empty on single-tenant servers, no `owner_admins designation … REFUSED` in logs).

## 5. B-14 activation requirements

* B-14 migrations were renumbered 196→201 and 197→202 after merging `agent/round6-base` (which owns 196–200). They were never deployed under the old numbers, so no `_migrations` alias is needed.
* Migration 201 (views `radius_source_tenant`, `radius_sole_tenant`; table `radius_unattributed`) **must exist before FreeRADIUS loads the new `mods-enabled/sql`** → the app migrates first, radius restarts after (never the other way round; never restart both at once).
* **Backfill: dry-run first** (`tools/sec_b14_backfill.py --db …`), owner reviews the report; **applying the backfill is a separate decision** (`--apply --undo-file /data/sec_b14_undo.json`; undo with `--rollback <file> --apply`). Single-tenant servers: nothing to migrate.
* **Unknown NAS fails closed** on multi-tenant servers: Access-Reject "Unknown NAS", accounting quarantined in `radius_unattributed` (ACKed), no webhook. Register every router on the right tenant **before** deploy (detection D6). Leftover demo/second tenant rows make a server count as multi-tenant — check D1.
* Single-tenant servers behave exactly as before.

## 6. B-22 order, `HOBERADIUS_ENV`, cookies

* **B-22: licence panel first** (`radius-module-admin` `agent/sec-fix-b22`: `531b5b3` test → `c289390` fix → `03d04a6` doc; 16/16 tests pass locally; base `62a4f3f` = production panel). The panel then signs `runtime-contract`/`capacity-contract` (`_bridge_sig`) and only the platform owner may edit owner designations. **Then** radius with this RC refuses unsigned designations. Reverse order is fail-safe but stalls designations (current designation / min-id fallback stays; nobody loses access). Verify panel HEAD on `control` before deploying it.
* **`HOBERADIUS_ENV` target = `production`** on every customer server (today unset everywhere). Effects: F-3 refuses a template/weak `FLASK_SECRET` at boot; F-5 seeds no default passwords (bootstrap admin gets a one-time password in `initial_admin_credentials.txt` next to the DB — read once, delete); the API dev-token fallback is disabled; session cookie `Secure` defaults ON.
* **Cookie rules**: `HttpOnly` always; `SameSite=Lax` (`HOBERADIUS_SESSION_COOKIE_SAMESITE`); `Secure` = ON in production by default. **A panel served over plain HTTP must set `HOBERADIUS_SESSION_COOKIE_SECURE=0`** or login breaks (cookie never sent). HTTPS-fronted panels keep the default.

## 7. Placeholder — `agent/sec-temp-password-api`

Merged (§1.5, `17fd3ed1` + interaction fix `0f70a133`). If that branch receives further owner-approved commits (e.g. mobile-contract follow-ups), merge them `--no-ff` into this RC and re-run §2 before release.

## 8. Rollback

* Fastest, no code: B-07 → `HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT=1` / `…_SERVER_WIDE=1`; F-3/F-5 boot loop → rotate FLASK_SECRET (preferred) or unset `HOBERADIUS_ENV`; B-08 → leave `HOBERADIUS_DIAG_ENABLED` unset.
* Code: redeploy the previous image (`825c7b45` on Abed/Barq/Fadi; `e9d652ee` on client1/spare-62) with `deploy.sh upgrade` (rebuild — code lives in the image).
* B-14 schema: migration 201 is additive (2 views + 1 table) — harmless to old code, leave it. The **old** `mods-enabled/sql`/`rest` must return together with the old image. If backfill was applied, roll it back with its undo file first.
* B-22: stored designations untouched; panel signing is backward compatible.
* X-Tenant (c) / temp-password: code-only; revert `8072e6ac` / `16ea2887` (+`0f70a133`) if a single fix must go.
* F-1 tokens `login:ui-mt:*` expire within 8 h. F-5 credentials file is an artefact to delete.
* Per-fix details: `SEC_FIXES_APP.md`, `SEC_FIX_B14.md`, `SEC_B22_RADIUS.md`, `SEC_F1.md`, `SEC_F3.md`, `SEC_F5.md`, `SEC_XTENANT_DECISIONS.md`, `SEC_TEMP_PASSWORD_API.md`, `SEC_MERGE_SIMULATION.md`.
