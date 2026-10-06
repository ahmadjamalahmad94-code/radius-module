# SEC — owner X-Tenant / token-scope decisions (2026-10-06)

Branch `agent/sec-xtenant-decisions` (from `agent/sec-merge-sim` 80cdf8d8). Local only — not pushed, not deployed.

## Decisions and where they are enforced

| # | Owner decision | Code | Status before this branch | Tests |
|---|---|---|---|---|
| (a) | A read-scope token can never call a mutating method | `app/api/permission_guard.py::token_scope_denial` (b38258a4), called first in `app/api/auth.py::enforce_api_auth` | **Already enforced.** Every read spelling (`read`, `readonly`, `read-only`, `read_only`, `api:read`, `admin:read`, case-insensitive) → only GET/HEAD/OPTIONS; owner tokens included; `read` + permission-like scope stays read-only. Only exemption: `admin_logout` (ends its own session). | `tests/test_hrai_scope_tenant_guard.py` (read cases), `tests/test_sec_xtenant_decisions.py::test_a_*` (POST for every spelling, PATCH/DELETE on `/api/v1/admins/<id>` with no change written, token revoke) |
| (b) | Unbound DB tokens (`created_by = 0`) need explicit scopes | same function: unbound + no `admin:full`/`*`/`write`/`admin:write`/read scope → 403 `token_scope`; B-07 (`unbound_token_denial`) further blocks minting and server-wide APIs | **Already enforced.** | `test_hrai_scope_tenant_guard.py::test_unbound_*`, `tests/test_sec_b07_unbound_tokens.py`, `test_sec_xtenant_decisions.py::test_b_*` (empty, `cards.view`, `dashboard.view`, unknown, blank → 403 on read too) |
| (c) | A Basic-auth member selects only one of HIS OWN ACTIVE tenants; the raw super-admin flag or a header alone never bypasses membership; only owner/co-owners roam | `app/api/auth.py::_resolve_admin_tenant` + `enforce_api_auth`; `app/radius/middleware/tenant_resolver.py::_header_tenant_allowed`; `app/radius/stores/tenants_store.py::selectable_tenants_for_admin` | **Partial → fixed here** (see gaps) | `test_sec_xtenant_decisions.py::test_c_*`, `test_web_*`; `test_hrai_scope_tenant_guard.py`; `tests/test_sec_b06_login_tenant_pick.py` |

## Gaps found in (c) and fixed (commit 8072e6ac, failing tests 8571fc1d)

1. **Raw `is_super_admin` without any membership** landed on `DEFAULT_TENANT_ID` over HTTP Basic (legacy fallback). Now → no tenant → 401.
2. **Tenant state was ignored**: a member could select (Basic `X-Tenant-Id`, web `X-Tenant`) or be defaulted into a `suspended` / `closed` tenant. Now "selectable" = active membership AND tenant status not in {`suspended`, `closed`} (`active`, `trial` usable).
3. **Silent re-route**: Basic `X-Tenant-Id` naming a foreign / suspended / removed-membership tenant (or a non-numeric value) silently fell back to the admin's first tenant, so a write meant for "B" landed in "A". Now refused: **403 `forbidden`, details.reason = `tenant_not_selectable`**.

Owner / co-owner (`is_owner_like`, read from the DB — never the raw flag or a role) still roam to any tenant, suspended included, on Basic and web.

Cross-tenant probes (A-only manager naming B): create admin in B → 403 and nothing written; list B's admins → 403.

## Unchanged / residual (documented, not part of (c))

* Bearer tokens: `X-Tenant`/`X-Tenant-Id` are ignored — the token's own tenant wins (`enforce_api_auth` re-points `g.tenant`).
* Anonymous requests (public store): `X-Tenant` slug still selects the tenant; the store key is verified against the handler's tenant (B-04).
* Login (web + `/api/admin/login`): B-06 rule (owner-level → any tenant; else memberships; no membership → default-tenant bootstrap creates a membership). Login does not yet skip suspended tenants — a login-time decision, out of scope for header selection.
* Env tokens (`HOBERADIUS_API_TOKENS`) remain full-scope, tenant 1.

## Behaviour change for integrations

* Basic callers that sent a wrong `X-Tenant-Id` and relied on the fallback now get 403 — fix the header or drop it.
* A raw-flag admin with no membership must be given a membership (or log in once on the web to bootstrap) before Basic API works.
* Admins of a suspended/closed tenant lose header/Basic access to it until reactivated (the owner still can).

## Rollback

Revert 8072e6ac (code) — tests 8571fc1d will then fail by design. No migration, no data change.
