# Security fixes — local merge simulation (2026-10-05)

Local only: nothing pushed, deployed or sent to a server. Branch
`agent/sec-merge-sim` (worktree `C:\Projects\rm-sec-merge-sim`) starts from
`agent/sec-integration` = `36646db3` (round6-base `611fc731` + X-Tenant /
read-only-scope fix `b38258a4`). Base comparisons used a detached worktree at
`36646db3` (`C:\Projects\rm-sec-merge-base`).

## 1. Merge order and result

| # | Branch (tip) | Merge commit | Conflicts | Sec tests after merge¹ |
|---|---|---|---|---|
| 1 | `agent/sec-fixes-app` (`a58b8138`) — B-04, B-06, B-07, B-08, B-13 | `c6e7e2ba` | none | 110 passed |
| 2 | `agent/sec-fix-b14` (`153b615d`) — RADIUS tenant attribution, migration 201 | `7c3fb128` | none (`internal_auth.py` auto-merged with B-08) | 131 passed |
| 3 | `agent/sec-fix-b22-radius` (`5c5f74c2`) — signed `owner_admins` | `8cfb9323` | none | 142 passed |
| 4 | `agent/sec-fix-f1-mtdash` (`46c55f6c`) — no env token in MikroTik pages | `bf8fba8c` | none | 151 passed |
| 5 | `agent/sec-fix-f3-secret` (`2aacf3f6`) — template values are weak secrets | `45cc5149` | none | 168 passed |
| 6 | `agent/sec-fix-f5-seed` (`991be106`) — no known default admin passwords | `40c8d8c9` | none (`admins_repo.py` auto-merged with B-13) | 176 passed |

¹ `tests/test_sec_*.py` + `tests/test_hrai_scope_tenant_guard.py` present at
that point (`tests/test_tenant_isolation*` does not exist in this tree).

All six merges were `--no-ff`, no textual conflicts. Overlapping files that
git merged automatically and that were reviewed by hand:

* `app/api/v1/internal_auth.py` — B-08 (`internal_diag` needs the internal
  secret) and B-14 (`internal_auth`/`internal_postauth` resolve the tenant from
  `Packet-Src-IP-Address`, reject/quarantine unattributed routers). Different
  functions; `_resolve_tenant_id` now returns `None` for "no single tenant" and
  its only remaining caller (`internal_postauth`) handles `None`. `internal_diag`
  keeps its explicit `tenant_id` from the body (diagnostic tool, now
  secret-gated) — intended.
* `app/radius/db/repos/admins_repo.py` — B-13 (tenant-scoped admin list/get)
  and F-5 (`ensure_bootstrap_admin` one-time password). Different functions.

## 2. Commits added on top of the merges

| Commit | What | Why |
|---|---|---|
| `154e380b` | `fix(sec)`: F-5 × F-3 — `initial_credentials.is_known_default()` also uses `secret_policy.is_weak_secret()` (+3 parametrised tests, fail before / pass after) | F-5 kept its own short list (`123456789`, `admin`…), F-3 introduced the real "publicly known" rule (placeholder wording + every value in a shipped env template). A production `HOBERADIUS_BOOTSTRAP_ADMIN_PASS=change-me-to-32-random-bytes-please` or `ChangeMe2026` was accepted as-is. Now one rule for both. |
| `64a15b5b` | `chore`: restore CRLF in `app/api/auth.py`, `mt_dashboard.py`, `mt_setup.py` | **Judgement call.** `agent/sec-fixes-app` and `agent/sec-fix-f1-mtdash` rewrote these CRLF files as LF (`core.autocrlf=false`, mixed-ending repo): ~80 real lines became a 2.4k-line whole-file diff that would conflict with every other branch touching these files. Content unchanged (`git diff -w` = the fixes only). If the source branches are merged elsewhere directly, apply the same normalisation. |
| `a54c0bb4` | `test(sec)`: `tests/test_sec_merge_interactions.py` | Fresh **production** boot: example FLASK_SECRET refused (F-3); one-time-password bootstrap admin still gets a tenant on web login (B-06 owner path) and is forced to `/account` (F-5); strict **xfail** recording the API gap below. |

Cosmetic, not changed: in `mt_dashboard._ui_api_token` (F-1) a line
continuation was collapsed into one long line
(`... == admin_id                     and int(...)`) — valid Python, worth tidying
in the F-1 branch.

## 3. Semantic interactions checked

| Pair | Finding |
|---|---|
| **F-1 × B-07 × b38258a4** | F-1 mints a DB token with `created_by` = session admin, `tenant_id` = session tenant, `scopes=["admin:full"]`, `login:` prefix, 8 h TTL. It is a **bound** token, so B-07 (`unbound_token_denial`) never applies; `token_scope_denial` lets `admin:full` through and `api_permission_denial` then applies the admin's own RBAC (owner bypass only for owner-level). b38258a4: the authenticated token tenant wins over `X-Tenant-Id` — covered by F-1's `test_tenant2_admin_token_is_bound_to_tenant2_and_cannot_jump` (passes on the merged tree). Bonus: before F-1 the dashboard used env token = tenant 1, so a tenant-2 session's dashboard read tenant-1 data; and with `HOBERADIUS_ENV=production` without `HOBERADIUS_API_TOKENS` the dashboard had no token at all. F-1 removes both, which makes turning production mode on (F-3/F-5) safe for the dashboard. |
| **F-1 × F-5 / password change** | Password change (`account_password`, `/api/admin/password`) calls `revoke_admin_tokens` → `login:ui-mt:*` tokens die; F-1's session cache re-checks `resolve_by_plain` and re-mints. A `must_change_password` admin never reaches the dashboard (web guard redirects first). OK. |
| **B-06 × F-5** | Fresh production install: the bootstrap admin is owner through the min-id fallback (`is_primary_owner`), so B-06 still lets him into a tenant; then the web forces the password change. Verified by `test_bootstrap_admin_web_login_lands_in_tenant_then_forced_change`. Even if he were not owner-level, both login paths bootstrap a default-tenant membership, so no lock-out. |
| **F-5 API gap (pre-existing, sharper now)** | `POST /api/admin/login` and HTTP Basic ignore `must_change_password` and do not expose it in the response, so the one-time password (and identity-sync initial passwords) keeps working on the API forever. Recorded as strict xfail; **owner decision**: refuse with a dedicated error, or return the flag and let the app force the change (app change). Not fixed here — changes the mobile-app contract. |
| **B-22 × B-06 × F-5** | B-06 makes owner-level depend on `is_owner_like` → owner designation. B-22 refuses unsigned `owner_admins`: the current designation (or the min-id fallback) stays. Fail-safe: nobody loses access; new designations stall until the panel signs. Hence panel B-22 first. |
| **B-14 migration number** | Only new migration was `201_sec_b14_radius_source_tenant.sql`; at the time no other local branch had a 196+ file. Later `agent/round6-base` took 196–200, so B-14 was renumbered to `201_sec_b14_radius_source_tenant.sql` / `202_sec_b14_radius_local_nas.sql` (never deployed under the old numbers). Pre-existing duplicate prefixes `027`, `085`, `164` are distinct filenames (runner keys by filename) — unchanged. |
| **B-14 × FreeRADIUS** | `mods-enabled/sql` reads the views `radius_source_tenant` / `radius_sole_tenant` and writes `radius_unattributed` — they must exist **before** radiusd loads the new config, i.e. the app (which runs migrations at boot) must start first. `radiusd -XC` could not be run here (no FreeRADIUS binary on this machine). |
| **B-14 × B-08** | Same file, separate endpoints; diag stays body-tenant + secret-gated. OK. |
| **F-3 × test fixtures** | 45 distinct `FLASK_SECRET` literals in `tests/` — none is weak under the new policy; the 5 test files that boot production use strong values. No fixture breaks. |
| **F-3 × F-5 × `HOBERADIUS_ENV`** | Both only bite with `HOBERADIUS_ENV`/`FLASK_ENV=production`; `deploy/.env.example` does not set it. Turning it on also: disables the API dev-token fallback, sets the session cookie `Secure` (plain-HTTP panels need `HOBERADIUS_SESSION_COOKIE_SECURE=0`), and **refuses boot** on a template FLASK_SECRET → rotate the secret before flipping the env. |

## 4. Migrations and boot (merged tree)

* Fresh SQLite, `run_pending_migrations()`: **191 applied in name order, 0 on
  re-run**; views `radius_source_tenant`, `radius_sole_tenant` and table
  `radius_unattributed` exist.
* `create_app()` with `HOBERADIUS_ENV=production`:
  random 64-hex FLASK_SECRET → **boots** (and F-5 writes
  `initial_admin_credentials.txt` next to the DB; not printed);
  `change-me-to-32-random-bytes-please`, `replace-with-a-long-random-flask-secret`,
  `dev-secret-change-me` → **RuntimeError (refused)**.

## 5. Test results vs base

**All security tests together** (`tests/test_sec_*.py` + `tests/test_hrai_scope_tenant_guard.py`,
one pytest run, merged tree `a54c0bb4`): **181 passed, 1 xfailed** (the documented
API must-change gap). Base `36646db3` has 9 `test_sec_*` files (53 tests) — all
still pass in the merged tree.

**Broad related subset, file-by-file** (full 900-file suite not feasible on this
shared machine: ~25 s/file): 243 non-`test_sec_` files whose names match
auth|token|tenant|radius|accounting|internal|migrat|admin|store|mt_|seed|config|
secret|login|owner|rbac|perm|bridge|license|session|nas|i18n_no_leak, plus every
`test_sec_*` file, each in its own pytest process:

| Tree | Files | Passed | Failed | xfailed |
|---|---|---|---|---|
| base `36646db3` (non-sec 243) | 243 | 2679 | 2 | 0 |
| merged `a54c0bb4` (non-sec 243) | 243 | 2679 | 2 | 0 |
| base `test_sec_*` | 9 | 53 | 0 | 0 |
| merged `test_sec_*` | 21 | 166 | 0 | 1 |

The only failures, identical on both trees (same 8 `E` lines, diffed):
`tests/test_i18n_no_leak_guard.py::test_inventory_wrapped_literals_reach_catalog`
and `::test_every_wrapped_msgid_is_in_catalog` — temp_speed / admin_alerts
msgids, **pre-existing**. **New failures introduced by the merge: 0.**
(Both runners hit the 2 h background limit once and were resumed on the
remaining files; every listed file has exactly one result.)

## 6. Combined deployment order (recommendation)

0. **Before anything (read-only, per server):**
   * token audit: list `api_tokens` rows with `created_by = 0` (unbound) and
     their scopes — after B-07 they cannot mint tokens or reach
     `tenants_*`/`backups_*`/server-wide admin list; empty/unknown-scope unbound
     tokens get 403 (b38258a4). Decide per integration; opt-outs
     `HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT` / `…_SERVER_WIDE` only if audited.
   * rotate the env API token that F-1 says was exposed in page HTML
     (`HOBERADIUS_API_TOKENS`); update HobeHub with the new value.
   * FLASK_SECRET audit: any template/placeholder value must be rotated
     **before** production mode is on (rotation logs everyone out and makes
     at-rest values encrypted with the old key unreadable).
   * B-14: `sqlite3 -readonly … < tools/sec_b14_detect.sql` (D1);
     `tenants_total = 1` → nothing to migrate.
   * live admins still on `123456789`/`admin` (F-5 does not touch existing ones).
1. **Licence panel: B-22 signing** first (runtime-contract `_bridge_sig`).
2. Build one image from this merged tree; in the image run
   **`radiusd -XC`** with the new `mods-enabled/sql` + `rest` (B-14).
3. Env on each server: strong `FLASK_SECRET` (rotated if needed),
   `HOBERADIUS_INTERNAL_SECRET` set (B-08 diag + RADIUS), then
   `HOBERADIUS_ENV=production` (+ `HOBERADIUS_SESSION_COOKIE_SECURE=0` on plain-HTTP
   panels); for new VPS: a strong `HOBERADIUS_BOOTSTRAP_ADMIN_PASS` or read
   `initial_admin_credentials.txt` and delete it after first login.
4. Backup DB → start the **app** container (applies migration 201) → check
   `_migrations` has `201_sec_b14_radius_source_tenant.sql` → then restart
   **freeradius** (it needs the 201 views).
5. Smoke: web login (owner + a tenant manager), MikroTik dashboard counters
   (F-1 token `login:ui-mt:*`), a RADIUS Access-Accept from a known router,
   `radius_unattributed` empty on single-tenant servers, logs free of
   `owner_admins designation … REFUSED` (B-22 panel signing works).
6. Multi-network servers only: B-14 backfill dry-run
   (`tools/sec_b14_backfill.py --db …`) → owner decision → apply.

## 7. Combined rollback plan

* **Fastest behavioural rollback without code:** B-07 → set
  `HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT=1` and `…_SERVER_WIDE=1`; F-3/F-5 → unset
  `HOBERADIUS_ENV=production` (dev behaviour; not a security fix, only to recover
  a boot loop); B-08 → `HOBERADIUS_DIAG_ENABLED` unset (endpoint off).
* **Code:** redeploy the previous image (`agent/sec-integration`-based). All
  fixes are code-only except B-14.
* **B-14 schema:** migration 201 only adds two views and one table — harmless to
  the old code, leave them in place. The **old** `mods-enabled/sql`/`rest` must go
  back together with the old image (the new sql config needs the views but the
  old one does not reference them). If the backfill was applied, roll it back with
  `tools/sec_b14_backfill.py --rollback <undo-file> --apply` (see `SEC_FIX_B14.md`) before or independently of the code rollback.
* **B-22:** stored owner designations are untouched; panel signing is backward
  compatible with old radius code (extra field ignored).
* **F-1:** leftover `login:ui-mt:*` tokens expire within 8 h (revoke via «رموز API»
  if needed).
* **F-5:** accounts created with one-time passwords stay; the credentials file is
  only an artefact to delete.
* **F-3:** if a server refuses to boot after deploy, the fix is to rotate
  FLASK_SECRET, not to roll back.
* Per-fix revert targets: see `SEC_FIXES_APP.md`, `SEC_FIX_B14.md`,
  `SEC_B22_RADIUS.md`, `SEC_F1.md`, `SEC_F3.md`, `SEC_F5.md`; merge-sim additions
  `154e380b` (revert = F-5 back to its own list), `64a15b5b` (line endings only),
  `a54c0bb4` (tests only).
