# SEC — temporary / one-time password must be changed before API use

Branch: `agent/sec-temp-password-api` (from `agent/sec-merge-sim`).
Owner decision (2026-10-06): a temporary or one-time password (F-5 bootstrap
password, admin-reset password, licence-panel initial password — anything that
sets `admins.must_change_password = 1`) MUST be changed before normal API use,
enforced on the server. The web panel already forced it at first login
(`radius.account` redirect) and keeps doing so.

## Gap that was closed

`POST /api/admin/login` ignored `must_change_password` and minted a normal
7-day `admin:full` token; HTTP Basic accepted the one-time password on every
endpoint. The F-5 bootstrap password therefore worked forever on the mobile
app / API. Recorded as a strict xfail in `tests/test_sec_merge_interactions.py`
(now a normal passing test).

## Behaviour

| Credential | Admin flag | Result |
|---|---|---|
| `POST /api/admin/login` (right password) | `must_change_password = 1` | 200, restricted credential (below) |
| `POST /api/admin/login` | `0` | unchanged normal session (+ new fields `token_type: "session"`, `must_change_password: false`) |
| Restricted credential (scope `password_change`) | any | only the 3 allowed endpoints; else 403 `PASSWORD_CHANGE_REQUIRED` |
| Any other DB token bound to the admin (older app token, F-1 dashboard token, integration token) | `1` | same restriction as above while the flag is set |
| HTTP Basic (right password) | `1` | 403 `PASSWORD_CHANGE_REQUIRED` on every endpoint (the change needs the Bearer credential) |
| HTTP Basic (wrong password) | any | unchanged 401 `unauthorized` (no flag oracle) |
| Env tokens (`HOBERADIUS_API_TOKENS`), unbound DB tokens | — | unaffected (no admin) |

`X-Tenant-Id` / `X-Tenant` cannot widen the restricted credential: the check
runs on the endpoint before any tenant/permission logic, and the token's tenant
is the one stored at login.

Enforcement point: `app/api/auth.py::enforce_api_auth` (the single choke point
for Bearer / `X-API-Key` / Basic). The refusal is returned **before**
`g._api_authed` is set, so the decorator-after-global-guard short-circuit can
never let it through.

## API contract (mobile)

### Login of a flagged admin — `POST /api/admin/login` → 200

```json
{"ok": true, "data": {
  "token": "<plaintext, shown once>",
  "token_id": 123,
  "token_type": "password_change",
  "must_change_password": true,
  "restricted": true,
  "allowed_endpoints": ["GET /api/admin/me", "POST /api/admin/password", "POST /api/admin/logout"],
  "admin": {"id": 7, "username": "...", "must_change_password": true, "...": "..."},
  "tenant_id": 1,
  "permissions": [],
  "grants": {},
  "expires_at": "2026-10-06T10:15:00Z"
}}
```

The credential lives **15 minutes** (`_PWCHANGE_TTL`), scope
`["password_change"]`, name `login:pwchange:<username>:<ts>` (the `login:`
prefix makes every existing revocation path cover it).

### Refusal — any other endpoint → 403

```json
{"ok": false, "error": {
  "code": "PASSWORD_CHANGE_REQUIRED",
  "message": "لأمانك، يجب تغيير كلمة المرور قبل المتابعة.",
  "details": {"reason": "password_change_required", "must_change_password": true,
              "allowed_endpoints": ["GET /api/admin/me", "POST /api/admin/password", "POST /api/admin/logout"]}
}}
```

### Allowed endpoints with the restricted credential

* `GET /api/admin/me` → `{admin, tenant_id, must_change_password: true, restricted: true, allowed_endpoints, permissions: [], grants: {}}` (no `system`, no grants).
* `POST /api/admin/password` — body unchanged `{current_password, new_password, confirm_password}`; same validation / throttle. Success:
  `{"updated": true, "source": "local"|"license_admin", "reauth_required": true, "message": "..."}`.
  Effects: flag cleared; the restricted credential **and every other login token** of the admin revoked (`revoke_admin_tokens`, `login:` prefix); web session epoch bumped (all web sessions end). Integration tokens without the `login:` prefix are kept (they are not login credentials; they work normally again once the flag is cleared). The client must log in again with the new password.
* `POST /api/admin/logout` — revokes the restricted credential.

### Normal sessions

Login and `/api/admin/me` gain `token_type: "session"`, `must_change_password: false`,
`restricted: false`, and `admin.must_change_password`. `POST /api/admin/password`
gains `reauth_required` (`false` → unchanged legacy behaviour: the calling
token is kept, other sessions revoked).

## Mobile contract change (Flutter `agent/temp-password-flow`)

* `must_change_password: true` / `token_type: "password_change"` in the login
  answer or `/api/admin/me`, or `PASSWORD_CHANGE_REQUIRED` from any call →
  `AuthState.mustChangePassword`; the router allows only `/change-password`
  (no shell, no deep link, back blocked) until the change succeeds; then the
  app signs in again with the new password.
* **Older app builds** do not know the flag: after login with a temporary
  password they get empty grants and 403 errors on every screen
  (server-enforced). The password can still be changed from their «حسابي»
  screen if it opens (`POST /api/admin/password` is allowed) — the server then
  revokes the token and the old app falls back to the login screen on the next
  401 — or on the web panel. Recommended: ship the new app build first.

## Migration impact

* No schema change (uses `admins.must_change_password`, migration 143).
* Admins whose flag is set right now (fresh F-5 installs, admin-reset or
  licence-panel initial passwords never changed) lose normal API / Basic access
  — including already-issued app tokens and integration tokens bound to them —
  until they change the password. Existing admins without the flag: no change.
* Integrations that authenticate with HTTP Basic as a flagged admin will get
  403 `PASSWORD_CHANGE_REQUIRED`; change that admin's password once.

## Rollback

Revert the fix commit on this branch (tests commit can stay as documentation,
re-marked xfail). Nothing to migrate back; issued restricted tokens expire in
15 minutes, or `UPDATE api_tokens SET revoked=1 WHERE name LIKE 'login:pwchange:%'`.
Operational escape hatch without a deploy: clear the flag for an admin
(`UPDATE admins SET must_change_password = 0 WHERE id = ?`) — this re-opens the
gap for that admin, so prefer changing the password.

## Tests

* `tests/test_sec_temp_password_api.py` (15) — login contract, refusal on a
  sample (accounts, nas, admins, roles, tokens GET/POST, accounts POST), allowed
  endpoints, `X-Tenant-Id` / `X-Tenant` cannot widen, cross-tenant (tenant-A
  admin vs tenant B), Basic refused (also with tenant header), pre-existing full
  token of a flagged admin restricted, change → temp + other login tokens
  revoked, old password refused, normal login works, wrong current password
  keeps the restriction, restricted scope survives clearing the flag, unflagged
  admin unchanged (login, Basic, change keeps the session), env token
  unaffected, F-1 dashboard token unaffected for a normal admin.
* `tests/test_sec_merge_interactions.py::test_api_login_refuses_or_flags_must_change_password`
  — the former strict xfail, now passing.
