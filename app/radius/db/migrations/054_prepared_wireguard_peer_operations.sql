-- Setup Wizard server-side WireGuard peer operation ledger.
-- Lab-only apply planning. No plaintext private keys are stored here.

CREATE TABLE IF NOT EXISTS prepared_wireguard_peer_operations (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  prepared_peer_id         INTEGER NOT NULL,
  registry_id              INTEGER NOT NULL,
  wizard_run_id            INTEGER,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  operation_type           TEXT NOT NULL,
  status                   TEXT NOT NULL DEFAULT 'planned',
  command_preview          TEXT NOT NULL DEFAULT '',
  rollback_preview         TEXT NOT NULL DEFAULT '',
  result_json              TEXT NOT NULL DEFAULT '{}',
  error_json               TEXT NOT NULL DEFAULT '{}',
  safety_warnings_json     TEXT NOT NULL DEFAULT '[]',
  created_at               TEXT NOT NULL,
  applied_at               TEXT NOT NULL DEFAULT '',
  rolled_back_at           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_prepared_wg_peer_operations_peer
  ON prepared_wireguard_peer_operations (tenant_id, prepared_peer_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_prepared_wg_peer_operations_registry
  ON prepared_wireguard_peer_operations (tenant_id, registry_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_prepared_wg_peer_operations_run
  ON prepared_wireguard_peer_operations (tenant_id, wizard_run_id, id DESC);
