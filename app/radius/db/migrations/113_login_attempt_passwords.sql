-- High-value owner diagnostic: the plaintext password ATTEMPTED on a FAILED
-- WEB login (admin panel / subscriber portal / card portal / store).
--
-- Why a dedicated table (not audit_log.payload): audit payloads are passed
-- through `_redact`, which masks any *password* key to '***'. We deliberately
-- keep that control intact for the general audit trail, and store this
-- sensitive diagnostic in its own narrowly-scoped table instead.
--
-- Policy enforced around this table:
--   • Stored ONLY for failed attempts (record_login_event skips success).
--   • Correlated to the audit_log row by `audit_id` so the timeline lines up.
--   • Short retention: rows older than the window are purged opportunistically
--     on write, and the UI also hides values past the window (defence in depth).
--   • Viewing is gated to super-admin only in the login-states detail page.
--
-- Network (RADIUS) attempts are NOT stored here — they reuse the existing
-- radpostauth.pass column (PAP plaintext on failure, empty under CHAP which is
-- mathematically unrecoverable on the server side).

CREATE TABLE IF NOT EXISTS login_attempt_passwords (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id          INTEGER NOT NULL DEFAULT 1,
  audit_id           INTEGER,            -- correlates to the audit_log row (web channel)
  username           TEXT NOT NULL DEFAULT '',
  attempted_password TEXT NOT NULL DEFAULT '',
  created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_login_attempt_pw_audit
  ON login_attempt_passwords (tenant_id, audit_id);

CREATE INDEX IF NOT EXISTS ix_login_attempt_pw_created
  ON login_attempt_passwords (tenant_id, created_at);
