-- Card package pricing and batch financial costing.
-- Additive only; existing card generation/auth behavior is unchanged.

ALTER TABLE card_marketplace_packages ADD COLUMN retail_price_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_marketplace_packages ADD COLUMN wholesale_price_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_marketplace_packages ADD COLUMN min_price_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_marketplace_packages ADD COLUMN max_discount_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_marketplace_packages ADD COLUMN allowed_manager_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE card_marketplace_packages ADD COLUMN allowed_distributor_ids_json TEXT NOT NULL DEFAULT '[]';

UPDATE card_marketplace_packages
SET retail_price_minor = price_minor
WHERE retail_price_minor = 0 AND price_minor > 0;

CREATE TABLE IF NOT EXISTS card_batch_financial_costs (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                 INTEGER NOT NULL DEFAULT 1,
  batch_id                  INTEGER NOT NULL,
  package_id                INTEGER,
  responsible_type          TEXT NOT NULL DEFAULT 'manager',
  responsible_id            INTEGER,
  created_by_type           TEXT NOT NULL DEFAULT 'admin',
  created_by_id             INTEGER,
  count                     INTEGER NOT NULL DEFAULT 0,
  retail_price_minor        INTEGER NOT NULL DEFAULT 0,
  wholesale_price_minor     INTEGER NOT NULL DEFAULT 0,
  total_retail_minor        INTEGER NOT NULL DEFAULT 0,
  total_wholesale_minor     INTEGER NOT NULL DEFAULT 0,
  wallet_id                 INTEGER,
  wallet_transaction_id     INTEGER,
  ledger_entry_id           INTEGER,
  revenue_record_id         INTEGER,
  price_snapshot_id         INTEGER,
  status                    TEXT NOT NULL DEFAULT 'posted',
  metadata_json             TEXT NOT NULL DEFAULT '{}',
  created_at                TEXT NOT NULL,
  FOREIGN KEY (batch_id) REFERENCES card_batches(id) ON DELETE RESTRICT,
  CHECK (status IN ('posted', 'voided', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_card_batch_financial_costs_batch
  ON card_batch_financial_costs (tenant_id, batch_id);
CREATE INDEX IF NOT EXISTS ix_card_batch_financial_costs_responsible
  ON card_batch_financial_costs (tenant_id, responsible_type, responsible_id, id DESC);
