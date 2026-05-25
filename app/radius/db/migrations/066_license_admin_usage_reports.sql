-- V40 admin bridge usage report attempts.
-- Stores sanitized usage payloads and send state only.

CREATE TABLE IF NOT EXISTS license_admin_usage_report_attempts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  report_window   TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  dry_run         INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL,
  payload_json    TEXT NOT NULL DEFAULT '{}',
  error_json      TEXT NOT NULL DEFAULT '{}',
  sent_at         TEXT,
  created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_license_admin_usage_attempts_latest
  ON license_admin_usage_report_attempts (tenant_id, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_admin_usage_idempotency
  ON license_admin_usage_report_attempts (tenant_id, idempotency_key, dry_run);
