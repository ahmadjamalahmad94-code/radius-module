-- V40 admin bridge service activation polling foundation.
-- Records requested service activation jobs and local dry-run execution state.

CREATE TABLE IF NOT EXISTS license_admin_service_activation_executions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id      INTEGER NOT NULL DEFAULT 1,
  reference      TEXT NOT NULL,
  service_key    TEXT NOT NULL,
  action_key     TEXT NOT NULL,
  status         TEXT NOT NULL,
  dry_run        INTEGER NOT NULL DEFAULT 1,
  adapter_key    TEXT NOT NULL DEFAULT '',
  payload_json   TEXT NOT NULL DEFAULT '{}',
  result_json    TEXT NOT NULL DEFAULT '{}',
  error_json     TEXT NOT NULL DEFAULT '{}',
  received_at    TEXT NOT NULL,
  executed_at    TEXT,
  callback_at    TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_admin_service_activation_reference
  ON license_admin_service_activation_executions (tenant_id, reference);

CREATE INDEX IF NOT EXISTS ix_license_admin_service_activation_latest
  ON license_admin_service_activation_executions (tenant_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_license_admin_service_activation_status
  ON license_admin_service_activation_executions (tenant_id, status);
