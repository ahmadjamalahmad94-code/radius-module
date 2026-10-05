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
