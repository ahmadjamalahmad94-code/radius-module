# SEC F-3: example-file FLASK_SECRET accepted in production

| | |
|---|---|
| Branch | `agent/sec-fix-f3-secret` (local only) |
| Base | `agent/sec-integration` @ `36646db3` |
| Commits | `6662d9ed` failing test · fix (see `git log`) · this doc |

## Problem
`create_app()` refused only four literal defaults. The placeholders shipped in
`.env.example` (`FLASK_SECRET=replace-with-…-flask-secret`) and
`deploy/.env.example` (`FLASK_SECRET=change-me-…-please`) were not on the list,
so `cp .env.example .env` + forgetting to edit booted production with a secret
that is public in the repo. FLASK_SECRET signs sessions and roots the at-rest
Fernet keys (`env_settings`, SMS/Telegram settings, store tokens).

## Fix
`app/secret_policy.py::is_weak_secret(value)`, used by `create_app()`. Weak =
1. empty / historical defaults / the literal placeholders of both templates
   (built in, because the image ships `deploy/` but not the root `.env.example`);
2. placeholder wording: `replace-with…`, `change-me…`, `changeme`, `your-…`,
   `…-here`, `placeholder`, `example`, `xxxx`, `<…>`, `${…}`;
3. any value present in an env template file (`*.env.example|sample|template|dist`)
   in the app root or under `deploy/` at runtime (cached).
Leading/trailing whitespace is ignored. Production (`HOBERADIUS_ENV`/`FLASK_ENV`
= prod/production) raises `RuntimeError`; dev/test only logs a warning (unchanged).

## Tests
`tests/test_sec_f3_example_secret_weak.py` (17): before 15 failed / 2 passed; after 17 passed.
Includes a drift guard: every value in every git-tracked env template is weak, and
every secret-like template value is weak even without the template files present.
Regression: `test_sec_session_secret`, `test_sec_internal_secret_failclosed`,
`test_stress_security_fixes`, `test_api_cors`, `test_demo_seed`: all pass (56 total with the new file).

## Behaviour change / migration impact
* **A production server whose `.env` still holds a template placeholder (or any
  value containing `example`, `change-me`, `replace-with`, `your-`, `-here`…) will
  refuse to start after this deploy** (container restart loop). Before deploying,
  run the read-only audit (`prod_readonly_audit.py`, check `E1`) on each server and
  rotate any weak FLASK_SECRET first (CREDENTIAL_REMEDIATION_PLAN §5.6, P8).
  Rotation logs everybody out and makes values encrypted with the old key unreadable
  (re-enter secret settings).
* No schema / data migration.

## Rollback
Revert the fix commit (code only) and rebuild the image.

## Owner decisions
1. Approve the boot refusal (fail-closed) vs. warn-only for a fleet transition period.
2. Optionally also require a minimum length (e.g. 32) in production; not done here
   because it could stop servers with short but random secrets.
