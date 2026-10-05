# SEC F-5: known default passwords from first-boot seeding

| | |
|---|---|
| Branch | `agent/sec-fix-f5-seed` (local only) |
| Base | `agent/sec-integration` @ `36646db3` |
| Commits | `efafdbda` failing test · `03af0b79` demo-seed fix · `3493c0bc` bootstrap-admin fix · this doc |

## Problem
1. `app/radius/seed.py::_seed_tenant_and_admins` (runs when `HOBERADIUS_NO_SEED` is unset;
   in production only with `HOBERADIUS_DEMO_SEED=1`) created `admin/admin` (super) and
   `operator/operator`.
2. `admins_repo.ensure_bootstrap_admin` runs on every boot with no admin, production
   included, and created `admin/123456789`, the value printed in `deploy/.env.example`.
   A fresh VPS was reachable with a public password until the owner logged in.

## Fix
New `app/radius/core/initial_credentials.py`. In production
(`HOBERADIUS_ENV`/`FLASK_ENV` = `prod|production`):
* demo seed: both admins get a random one-time password (`secrets.token_urlsafe(18)`);
* bootstrap admin: same, unless `HOBERADIUS_BOOTSTRAP_ADMIN_PASS` is set to a value that
  is **not** a known default (`123456789`, `admin`, `operator`, `password`, …), which is
  honoured as before;
* each such admin gets `must_change_password=1`, so the existing web guard
  (`auth/decorators.py`) forces a change at first login;
* credentials are appended to `<dir of HOBERADIUS_DB_PATH>/initial_admin_credentials.txt`
  (created `0600`, override `HOBERADIUS_INITIAL_CREDENTIALS_FILE`). Only the path is
  logged, never the password. In Docker this is `instance/` (bind mount), so on the host:
  `cat /opt/hoberadius/instance/initial_admin_credentials.txt` (as root), then delete it.
Dev/test: unchanged (`admin/admin`, `operator/operator`, `admin/123456789`).

## Tests
`tests/test_sec_f5_seed_defaults.py` (8): before 5 failed / 3 passed; after 8 passed.
Covers prod demo seed, prod bootstrap (default, explicit known default, explicit strong),
0600 mode (POSIX) + password absent from logs, no rotation on second boot, dev unchanged.
Regression run: `test_demo_seed`, `test_admin_login_bootstrap_and_500`,
`test_stress_security_fixes`, `test_admin_session_invalidation`, `test_owner_only_bypass`,
`test_balance_movements_currency_and_demo_cleanup`, `test_license_admin_runtime_sync`.

## Behaviour change / migration impact
* Existing servers: **no effect** (both paths only run when there is no admin yet).
  Existing `admin/123456789` accounts on live servers are NOT changed; they need a
  separate audit/rotation.
* New production VPS: the owner no longer logs in with `admin/123456789`. He reads the
  file above (or sets a strong `HOBERADIUS_BOOTSTRAP_ADMIN_PASS` before first boot).
  The handover checklist and the comment in `deploy/.env.example` must be updated.
* The `must_change_password` flag is enforced on the web only; the mobile app
  (`/api/admin/login`) still logs in with the one-time password.
* "Production" is decided by `HOBERADIUS_ENV`/`FLASK_ENV`. `deploy/.env.example` does
  **not** set it (the root `.env.example` does), so a server built from the deploy
  template is treated as dev and keeps the old defaults. Verify on each server.
* No schema migration.

## Rollback
Revert `3493c0bc` (bootstrap) and/or `03af0b79` (demo seed). Code only.

## Owner decisions
1. Accept the new-VPS step "read `initial_admin_credentials.txt`" (or always set a strong
   `HOBERADIUS_BOOTSTRAP_ADMIN_PASS` in provisioning). If not, drop `3493c0bc` and keep
   only the demo-seed fix.
2. Set `HOBERADIUS_ENV=production` in `deploy/.env.example` and on every server (affects
   F-3, F-5, the API dev-token fallback). Caveat: production also turns the session
   cookie `Secure` flag on by default, which breaks login on a plain-HTTP panel unless
   `HOBERADIUS_SESSION_COOKIE_SECURE=0` is set.
3. Audit live servers for an admin still on `123456789` / `admin`.
