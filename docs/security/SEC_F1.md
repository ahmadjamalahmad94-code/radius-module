# SEC F-1: env API token rendered into the MikroTik pages

| | |
|---|---|
| Branch | `agent/sec-fix-f1-mtdash` (local only) |
| Base | `agent/sec-integration` @ `36646db3` |
| Commits | `3980e2eb` failing test · `853bcbce` fix · this doc |

## Problem
`app/radius/routes/mt_dashboard.py::_ui_api_token()` returned the first value of
`HOBERADIUS_API_TOKENS` (or `dev-token-please-change` outside production) and both
`/admin/radius/mt/<id>/dashboard` and `/admin/radius/mt/operations`
(`mt_setup.py`) rendered it into `data-mt-api-token`. Env tokens are unbound,
`admin:full`, tenant 1, skip the scope check and every permission
(`permission_guard`). Any panel user who could open those pages could copy an
owner-level credential (CREDENTIAL_REMEDIATION_PLAN finding F-1).

## Fix
`_ui_api_token()` now returns a short-lived DB token for the **logged-in admin**:
* `created_by` = `session["admin_id"]`, `tenant_id` = the request tenant (`_tid()`);
* `expires_at` = now + 8 h; reused from the web session while > 30 min remain
  (one row per session, not per page view) and only while the row is still valid
  for the same admin + tenant;
* name `login:ui-mt:<user>:<ts>`: the existing `revoke_admin_tokens()` (password
  change / disable) kills it, and auth already rejects tokens of a deleted/disabled admin;
* scopes `["admin:full"]`, which narrows nothing; `/api/v1` applies the admin's own
  permissions via `permission_guard` because the token is bound. DB tokens ignore
  `X-Tenant-Id`, so the page token cannot jump tenants;
* no logged-in admin → `""` (the JS shows its "auth not configured" state).
Same model as the app's `/api/admin/login` tokens; no new mechanism, no new secret.

## Tests
`tests/test_sec_f1_mt_dashboard_token.py` (9): before 7 failed / 2 passed; after 9 passed.
HTML of both pages contains no configured env token and no dev fallback; token bound
to the session admin and tenant, expiring, reused, authenticates as that admin, dies
on revoke; tenant-2 admin's token is tenant 2 and `X-Tenant-Id: 1` neither switches
tenant nor lists tenant-1 NAS.
Regression: `test_mt_dashboard_*` (13 files), `test_connected_from_radacct`,
`test_ip_change_concept_separation`, `test_loop_broadcast_always_on`,
`test_api_auth_security`: 172 passed.

## Behaviour change / migration impact
* The dashboard JS now acts with the viewing admin's permissions, not owner
  permissions. A limited admin who opens the page may now get 403 on actions his
  role does not grant (correct, but visible).
* Rows named `login:ui-mt:*` appear in «رموز API» (one per web session, 8 h).
  Expired rows stay in the table like expired app-login tokens do.
* The page token is in the session cookie (signed, not encrypted): visible only to
  the same browser that already holds the panel session.
* A dashboard left open > 8 h gets 401 until reload.
* No schema migration. Env tokens are untouched: rotate the one that was exposed
  (plan §5.4), because every past viewer may have it.

## Rollback
Revert `853bcbce` and rebuild. Leftover `login:ui-mt:*` rows are harmless and expire.

## Owner decisions
1. Rotate the currently configured env token on each server (it was visible to all
   panel users who opened these pages).
2. TTL 8 h acceptable? (constant `_UI_TOKEN_TTL`).
