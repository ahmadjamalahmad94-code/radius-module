-- V40 admin bridge instance heartbeat attempts.
-- Stores sanitized health payloads and send state only.

CREATE TABLE IF NOT EXISTS license_admin_heartbeat_attempts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  idempotency_key TEXT NOT NULL,
  dry_run         INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL,
  payload_json    TEXT NOT NULL DEFAULT '{}',
  error_json      TEXT NOT NULL DEFAULT '{}',
  response_json   TEXT NOT NULL DEFAULT '{}',
  sent_at         TEXT,
  created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_license_admin_heartbeat_attempts_latest
  ON license_admin_heartbeat_attempts (tenant_id, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_admin_heartbeat_idempotency
  ON license_admin_heartbeat_attempts (tenant_id, idempotency_key, dry_run);
