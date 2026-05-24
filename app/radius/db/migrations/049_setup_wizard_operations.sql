-- Wave C: guarded setup wizard operation log.
-- Additive only. Live apply remains disabled by configuration by default.

CREATE TABLE IF NOT EXISTS setup_wizard_operations (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  wizard_run_id            INTEGER NOT NULL,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  step_key                 TEXT NOT NULL,
  operation_type           TEXT NOT NULL,
  operation_order          INTEGER NOT NULL DEFAULT 0,
  status                   TEXT NOT NULL DEFAULT 'planned',
  command_preview          TEXT NOT NULL DEFAULT '',
  command_applied          TEXT NOT NULL DEFAULT '',
  rollback_command         TEXT NOT NULL DEFAULT '',
  result_json              TEXT NOT NULL DEFAULT '{}',
  error_json               TEXT NOT NULL DEFAULT '{}',
  safety_warnings_json     TEXT NOT NULL DEFAULT '[]',
  created_at               TEXT NOT NULL,
  applied_at               TEXT NOT NULL DEFAULT '',
  rolled_back_at           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_operations_run
  ON setup_wizard_operations (tenant_id, wizard_run_id, operation_order ASC, id ASC);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_operations_status
  ON setup_wizard_operations (tenant_id, status, id DESC);
