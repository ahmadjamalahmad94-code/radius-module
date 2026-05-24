-- Setup Wizard recovery events.
-- This is an operator recovery/audit trail only; it stores no plaintext secrets.

CREATE TABLE IF NOT EXISTS setup_wizard_recovery_events (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  wizard_run_id      INTEGER NOT NULL,
  tenant_id          INTEGER NOT NULL DEFAULT 1,
  registry_id        INTEGER,
  event_type         TEXT NOT NULL,
  reason             TEXT NOT NULL DEFAULT '',
  action             TEXT NOT NULL DEFAULT '',
  result_json        TEXT NOT NULL DEFAULT '{}',
  created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_recovery_events_run
  ON setup_wizard_recovery_events (tenant_id, wizard_run_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_recovery_events_registry
  ON setup_wizard_recovery_events (tenant_id, registry_id, id DESC);
