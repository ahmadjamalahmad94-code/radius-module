# App-level tenant-isolation fixes (branch `agent/sec-fixes-app`)

- **Base:** `agent/sec-integration` (`36646db3` = `agent/round6-base` + reviewed X-Tenant fix `b38258a4`).
- **Scope:** the app-level bypasses from `hoberadius-ai-support/docs/security/BYPASS_PATHS.md`.
  Out of scope (owned elsewhere): B-14 (FreeRADIUS `tenant_id=1`), B-22 (licence-panel `owner_admins`).
- **Status:** local only. Nothing pushed, deployed or run against a server.
- **Tests:** shared fixtures in `tests/sec_tenant_helpers.py` (temporary SQLite per test, tokens minted inside it).
  One test module per bypass: `tests/test_sec_bXX_*.py`.

Each section: what was reproduced, the fix, behaviour change, migration impact, rollback.
Rollback is always "revert that commit" (`git revert <hash>`); no fix in this branch adds a migration.

---

## B-04 — store key skipped via anonymous `X-Tenant`

**Reproduced?** Not as described — **latent**. The static review assumed the store-key guard reads the
header's tenant. In the real app the guard (`app/__init__.py:104`) is registered *before* the tenant
resolver (`:110`), so `g.tenant_id` is still unset when the guard runs and it falls back to tenant 1.
All direct attack tests (keyless tenant B, B's own key, store-token calls) were already refused (403).
The guard was nonetheless only safe by accident of hook order: `test_guard_independent_of_hook_order`
inserts a "resolve tenant first" hook and the guard then opened tenant 1's store without its key
(**failed before the fix: `assert 200 == 403`**).

**Fix:** `app/api/v1/store.py` — the guard verifies the key against `_store_key_tenant()`: the tenant in
a valid signed store token, otherwise tenant 1 (the tenant the public handlers hard-code). It never
reads `g.tenant_id` / `X-Tenant`.

**Tests:** `tests/test_sec_b04_store_key_tenant.py` (7): sanity, keyless-tenant header on
ping/register/login, cross-tenant key (B's key + `X-Tenant: B` → 403, tenant 1's key still works),
store-token call, legacy no-key mode, `/store/admin/*` untouched, hook-order independence.

**Behaviour change:** none for legitimate clients (the published `store.html` sends tenant 1's key).
A client that sent *another tenant's* key together with `X-Tenant` is now refused (that was the hole).

**Migration impact:** none. **Rollback:** revert the commit.

---

## B-06 — raw `is_super_admin` («مدير عام») logs into the first tenant on the server

**Reproduced?** Yes. A raw-flag admin who is a member of tenant B only logged into **tenant 1** on both
paths (web `POST /admin/radius/login` → `session["tenant_id"]=1`; app `POST /api/admin/login` →
`tenant_id=1` and an `admin:full` token bound to tenant 1). 4 tests failed before the fix
(`assert 1 == 2`).

**Fix:** `app/radius/routes/auth.py` (web) and `app/api/admin_auth.py::_pick_tenant` (app): only
owner-level accounts (`is_owner_like` / `admins_repo.is_primary_owner`: licence-panel owner set,
original owner, co-owner — read from the DB) get `store.list()`; everyone else, including the raw
flag, uses `tenants_for_admin()` (active memberships). The bootstrap for an admin with **no**
membership (→ default tenant) is unchanged.

**Tests:** `tests/test_sec_b06_login_tenant_pick.py` (7): web + app reproducers; cross-tenant (two
raw-flag admins in B and C each land in their own tenant on both paths, and the minted token row is
bound to that tenant); multi-membership; owner and co-owner unchanged; no-membership bootstrap unchanged.

**Behaviour change:** a «مدير عام» who is not owner/co-owner lands in his first active membership
instead of the server's lowest tenant id. On single-tenant servers (the whole current fleet per the
fleet notes) there is no visible change.

**Residual (not changed, needs OWNER DECISION):** an admin with **no** membership row is still
bootstrapped into the default tenant (tenant 1) at first login, and gets a membership there. On
mainline nothing else creates memberships (admins created on `/admins` have none), so this bootstrap
is how every new admin gets a tenant today. On a real multi-tenant server it means "any admin without
a membership joins tenant 1". Restricting it requires an admin-creation flow that writes memberships
first.

**Migration impact:** none. **Rollback:** revert the commit.

---

## B-07 — unbound API tokens (`created_by=0`)

**Already closed by `b38258a4` (re-verified):** an unbound token with no / unknown scope (`[]`,
`["cards.view"]`, `["bogus"]`) gets 403; `read` is GET-only.

**Reproduced (what remained)?** Yes, 4 tests failed before the fix:
1. an unbound `admin:full`/`*` token minted further unbound tokens with any scope (`*` included) —
   `POST /api/v1/tokens` → 201. A leaked token could perpetuate itself and survive its own revocation;
2. an unbound token is "owner-level", so a token bound to **tenant B** listed every tenant
   (`GET /api/v1/tenants`), read tenant A (`GET /api/v1/tenants/1`), **renamed tenant A**
   (`PATCH /api/v1/tenants/1` → 200) and reached the whole-database backups (`/api/v1/backups/*`).

**Fix:** `app/api/permission_guard.py::unbound_token_denial()`, called from
`app/api/auth.py::enforce_api_auth` right after the scope check. It applies only to **DB tokens with
`created_by=0`**:
- `v1.tokens_create` → 403 (`details.reason="unbound_token"`);
- `v1.tenants_*`, `v1.backups_*` → 403.

Listing/revoking tokens and all tenant-scoped endpoints are unchanged. Env tokens
(`HOBERADIUS_API_TOKENS`, configured by the operator on the server), HTTP Basic, and tokens bound to an
admin (incl. the owner) are unchanged. The refusal reuses an existing catalogue message
(`هذا الإجراء مقصور على المالك أو الشريك.`) so no new i18n entries are needed.

**OWNER DECISION — settings (safe default = refuse):**

| Env var | Default | `=1` restores |
|---|---|---|
| `HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT` | off | unbound DB tokens may mint tokens (old behaviour) |
| `HOBERADIUS_ALLOW_UNBOUND_TOKEN_SERVER_WIDE` | off | unbound DB tokens reach `tenants_*`, `backups_*`, and the server-wide admin list (B-13) |

Before deploying: run `READONLY_AUDIT_QUERIES.sql` §1-§3 on each server. If an integration (HobeHub,
licensing bridge) holds an **unbound DB token** and calls `/tokens` (POST), `/tenants` or `/backups`,
either re-issue it from the web «رموز API» page (bound to a dedicated admin) or set the matching flag.
HobeHub per `INTEGRATION_WITH_HOBEHUB.md` uses `HOBERADIUS_API_TOKENS` (env) — not affected.

**Tests:** `tests/test_sec_b07_unbound_tokens.py` (15): mint refused (`*` and default scope; no row
created), mint flag restores, list still OK, bound-owner and env tokens still mint; tenant-B unbound
token cannot list/get/PATCH tenant A (A unchanged) nor reach backups; server-wide flag restores; owner
and env tokens keep server-wide access; **cross-tenant**: same username `ahmad` in A and B, each
unbound token sees only its own row with no header, `X-Tenant` or `X-Tenant-Id`; no-scope tokens still
refused. `tests/test_hrai_scope_tenant_guard.py::test_unbound_token_flagged_full_keeps_behaviour`
updated: minting is 403 by default and 201 with the flag.

**Migration impact:** none (env flags only). **Rollback:** revert the commit, or set both flags to `1`
for an immediate behavioural rollback without a redeploy of code.

---

## B-08 — `/api/v1/internal/_diag` unauthenticated password oracle

**Reproduced?** Yes (with `HOBERADIUS_DIAG_ENABLED=1`). With no credential at all the endpoint
returned `found_in` and `decision.ok` for any `username`/`password`/`tenant_id` — 3 tests failed
before the fix (`assert 200 in (401, 403)`), including the case where no internal secret is configured
outside production.

**Fix:** `app/api/v1/internal_auth.py::internal_diag` — after the existing `HOBERADIUS_DIAG_ENABLED`
gate it now requires (a) `HOBERADIUS_INTERNAL_SECRET` to be configured (else 403; the generic
"accept without secret in dev" fallback does **not** apply to this endpoint) and (b) the secret in
`X-Internal-Secret` or the body field `_internal_secret` (else 401), via the same
`_check_internal_secret` that FreeRADIUS calls use. The secret field is removed from the body before use.

**Tests:** `tests/test_sec_b08_internal_diag.py` (5): no secret / wrong secret (header and body) refused
and the response carries no `found_in`/`decision`; refused when no secret is configured even in dev;
**cross-tenant**: same username `ahmad` with different passwords in A and B — with the secret each
tenant answers for its own row, and A's password is rejected in B and vice versa; disabled by default
still 403.

**Behaviour change:** operators using the diag curl must add
`-H "X-Internal-Secret: $HOBERADIUS_INTERNAL_SECRET"` (docstring updated). Default (flag off): none.

**Migration impact:** none. **Rollback:** revert the commit (or keep `HOBERADIUS_DIAG_ENABLED` unset,
which is the default and makes the endpoint return 403 regardless).

---

## B-13 — admin accounts are server-global (list / get / edit / delete)

**Reproduced?** Yes — and the "cross-tenant edit/delete" part that the review left SUSPECTED is
**confirmed**. 8 tests failed before the fix: a tenant-B manager with `admins.view/edit/delete`
- saw tenant A's admins in `GET /api/v1/admins` and on the web `/admin/radius/admins` page;
- read a tenant-A admin by id (`GET /api/v1/admins/<id>` → 200, web `/admins/<id>/edit` → 200);
- **modified** a tenant-A admin (`PATCH /api/v1/admins/<id>` → 200);
- an unbound DB token of tenant B also listed everyone;
- an admin created by the tenant-B manager had no membership, so it showed up in tenant A's list and
  was bootstrapped into tenant 1 at first login.

**Fix:**
- `admins_repo.admin_ids_in_tenant(tid)` / `admin_in_tenant(aid, tid)`: an admin belongs to a tenant
  through an **active** `tenant_memberships` row; in the default tenant (1) admins with no active
  membership anywhere also belong (exactly the accounts the login bootstrap attaches to tenant 1 — this
  keeps single-tenant servers unchanged).
- API `app/api/v1/admins.py`: `admins_list` filtered; `admins_get/patch/delete` return **404** for an
  admin outside the caller's tenant. Server-wide callers: owner / co-owner behind the token, env
  tokens, and unbound DB tokens only with `HOBERADIUS_ALLOW_UNBOUND_TOKEN_SERVER_WIDE=1` (B-07).
- Web `app/radius/routes/admins.py`: `admins_list` and `admins_profile_summary` filtered;
  `admins_edit/update/delete` → 404 outside the tenant. Server-wide: owner / co-owner (`is_owner_like`).
  «مدير عام» is tenant-scoped.
- Create (API and web): the new admin gets an active membership in the creator's current tenant, so it
  stays visible to its creator and lands in that tenant at first login (closes the B-06 residual for
  admins created after this change).

**Tests:** `tests/test_sec_b13_admin_list_scope.py` (10): API list/get/patch/delete and web
list/edit/update/delete scoped (tenant-A admin unchanged afterwards); **cross-tenant symmetric**
(managers of A and B each see only their side; `X-Tenant`/`X-Tenant-Id` do not widen it); unbound
token of B scoped; created-by-B admin stays in B (and app login lands in B); owner still sees everyone;
membership-less legacy admins still listed in the default tenant.

**Behaviour change:** on multi-tenant servers a non-owner manager only sees/manages admins of his
current tenant. Single-tenant servers: no change (every admin is in tenant 1, either by membership or
by having none). Roles (`/roles`) remain server-global (not changed here).

**Migration impact:** none (no schema change). Existing admins without membership keep appearing in
tenant 1 only. **OWNER DECISION** for multi-tenant servers: decide which tenant each existing
membership-less admin belongs to and insert memberships (read-only check:
`SELECT id, username FROM admins a WHERE deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM
tenant_memberships m WHERE m.admin_id=a.id AND m.status='active');`).

**Rollback:** revert the commit. Memberships created for new admins by the fixed create path are
harmless after a revert (they match what the login bootstrap would have done on a single-tenant server).

---

## Not fixed here (and why)

| ID | Reason |
|---|---|
| B-14 FreeRADIUS `tenant_id=1`, B-22 licence-panel `owner_admins` | Owned by other work streams (out of scope). |
| B-09 hotspot analytics `?t=` | Integrity-only, low. A real fix needs a signed per-tenant token in the published hotspot pages (redeploy of every router page), not an app-only change. |
| B-10 / B-11 | Default-tenant behaviour, not a leak. |
| B-12 weak `FLASK_SECRET` outside production | SUSPECTED / configuration — server audit (`DEPLOYMENT_CHECKLIST` step 2). Related: outside production `app/api/auth.py` also accepts a built-in dev fallback API token as an env (owner-level) token. |
| B-23 hosting branch | Different branch; needs its own port. |
| B-24 `read` scope method-based | SUSPECTED; behaviour change, owner decision. |
| B-25 token in `HANDOFF_2026-05-20.md` | Not a code fix: the token must be **revoked** on every server (by hash). Editing the file does not remove it from git history. OWNER ACTION. |

## Owner decisions (summary)

1. **B-07:** keep `HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT` and `HOBERADIUS_ALLOW_UNBOUND_TOKEN_SERVER_WIDE`
   off (default) unless an audited integration with an **unbound DB token** needs them.
2. **B-06 / B-13:** on multi-tenant servers, assign memberships to existing admins that have none
   (they stay in tenant 1 by default, and are bootstrapped there at login).

## Test runs (local, temporary SQLite only)

- Each reproducer was run **before** its fix and failed: B-04 1 (latent hook-order test), B-06 4,
  B-07 4, B-08 3, B-13 8 failures; after each fix the module passes (B-04 7, B-06 7, B-07 15, B-08 5,
  B-13 10).
- Related modules per fix (store, login/owner/super-admin, tokens/permission guard/backups, internal
  auth, admins/roles/manager grants): all pass.
- **Full suite on this branch (`c9877a11`), one pytest process per file:** 894 files —
  **10063 passed, 11 skipped, 2 failed**. The 2 failures are
  `tests/test_i18n_no_leak_guard.py::{test_inventory_wrapped_literals_reach_catalog,
  test_every_wrapped_msgid_is_in_catalog}` — **pre-existing** on `agent/sec-integration` (same 2 fail
  there; missing catalogue entries for `services/temp_speed.py` and `services/admin_alerts.py`). This
  branch adds no new missing msgid (B-07 reuses an existing message).
- Note: in one combined multi-module run, a few `test_operations_foundation.py` /
  `test_api_customer_contracts.py` tests failed; they pass on their own and in the per-file full run
  (order-dependent, not caused by these changes).
