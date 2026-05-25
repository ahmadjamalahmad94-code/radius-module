-- Manager/distributor operational wallet, permissions, limits, and profit metadata.
-- Additive only; existing admins/distributors/subscribers behavior is unchanged.

CREATE TABLE IF NOT EXISTS manager_distributor_policies (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  entity_type            TEXT NOT NULL,
  entity_id              INTEGER NOT NULL,
  permissions_json       TEXT NOT NULL DEFAULT '{}',
  limits_json            TEXT NOT NULL DEFAULT '{}',
  profit_share_percent   REAL NOT NULL DEFAULT 0,
  credit_limit_minor     INTEGER NOT NULL DEFAULT 0,
  require_approval_above_minor INTEGER NOT NULL DEFAULT 0,
  status                 TEXT NOT NULL DEFAULT 'active',
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  created_at             TEXT NOT NULL,
  updated_at             TEXT,
  CHECK (entity_type IN ('manager', 'distributor')),
  CHECK (status IN ('active', 'disabled'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_manager_distributor_policy_entity
  ON manager_distributor_policies (tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_manager_distributor_policy_status
  ON manager_distributor_policies (tenant_id, entity_type, status);

CREATE TABLE IF NOT EXISTS manager_distributor_operations (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id        INTEGER NOT NULL DEFAULT 1,
  entity_type      TEXT NOT NULL,
  entity_id        INTEGER NOT NULL,
  operation_key    TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'recorded',
  amount_minor     INTEGER NOT NULL DEFAULT 0,
  reference_type   TEXT NOT NULL DEFAULT '',
  reference_id     INTEGER,
  result_json      TEXT NOT NULL DEFAULT '{}',
  created_by       TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL,
  CHECK (entity_type IN ('manager', 'distributor'))
);

CREATE INDEX IF NOT EXISTS ix_manager_distributor_operations_entity
  ON manager_distributor_operations (tenant_id, entity_type, entity_id, id DESC);
