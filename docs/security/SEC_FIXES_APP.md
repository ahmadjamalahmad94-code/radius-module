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
