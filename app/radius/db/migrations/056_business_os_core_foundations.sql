-- Business OS core financial/event foundations.
-- Additive only. No existing RADIUS auth/accounting tables are rewritten.

CREATE TABLE IF NOT EXISTS wallets (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  owner_type             TEXT NOT NULL,
  owner_id               INTEGER,
  balance_minor          INTEGER NOT NULL DEFAULT 0,
  pending_balance_minor  INTEGER NOT NULL DEFAULT 0,
  currency               TEXT NOT NULL DEFAULT 'JOD',
  status                 TEXT NOT NULL DEFAULT 'active',
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  created_at             TEXT NOT NULL,
  updated_at             TEXT,
  CHECK (owner_type IN ('company', 'manager', 'distributor', 'subscriber', 'card_user')),
  CHECK (owner_type = 'company' OR owner_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_wallets_owner
  ON wallets (tenant_id, owner_type, owner_id, currency);
CREATE INDEX IF NOT EXISTS ix_wallets_status
  ON wallets (tenant_id, status, owner_type);

CREATE TABLE IF NOT EXISTS wallet_transactions (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  wallet_id              INTEGER NOT NULL,
  transaction_type       TEXT NOT NULL,
  amount_minor           INTEGER NOT NULL,
  before_balance_minor   INTEGER NOT NULL,
  after_balance_minor    INTEGER NOT NULL,
  currency               TEXT NOT NULL DEFAULT 'JOD',
  reference_type         TEXT NOT NULL DEFAULT '',
  reference_id           INTEGER,
  actor_type             TEXT NOT NULL DEFAULT '',
  actor_id               INTEGER,
  notes                  TEXT NOT NULL DEFAULT '',
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  created_at             TEXT NOT NULL,
  FOREIGN KEY (wallet_id) REFERENCES wallets(id) ON DELETE RESTRICT,
  CHECK (transaction_type IN ('credit', 'debit', 'transfer', 'hold', 'release', 'reversal'))
);

CREATE INDEX IF NOT EXISTS ix_wallet_transactions_wallet_date
  ON wallet_transactions (tenant_id, wallet_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_wallet_transactions_reference
  ON wallet_transactions (tenant_id, reference_type, reference_id);
CREATE INDEX IF NOT EXISTS ix_wallet_transactions_actor
  ON wallet_transactions (tenant_id, actor_type, actor_id, id DESC);

CREATE TABLE IF NOT EXISTS ledger_entries (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  entry_type             TEXT NOT NULL,
  debit_account          TEXT NOT NULL,
  credit_account         TEXT NOT NULL,
  amount_minor           INTEGER NOT NULL,
  currency               TEXT NOT NULL DEFAULT 'JOD',
  actor_type             TEXT NOT NULL DEFAULT '',
  actor_id               INTEGER,
  target_type            TEXT NOT NULL DEFAULT '',
  target_id              INTEGER,
  reference_type         TEXT NOT NULL DEFAULT '',
  reference_id           INTEGER,
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  created_at             TEXT NOT NULL,
  voided_at              TEXT,
  reversal_of            INTEGER,
  FOREIGN KEY (reversal_of) REFERENCES ledger_entries(id) ON DELETE RESTRICT,
  CHECK (amount_minor > 0),
  CHECK (entry_type IN (
    'payment', 'renewal', 'debt', 'loan', 'discount', 'wallet_recharge',
    'card_sale', 'batch_creation', 'profit_share', 'reversal', 'correction'
  ))
);

CREATE INDEX IF NOT EXISTS ix_ledger_entries_tenant_date
  ON ledger_entries (tenant_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_type_date
  ON ledger_entries (tenant_id, entry_type, id DESC);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_reference
  ON ledger_entries (tenant_id, reference_type, reference_id);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_actor
  ON ledger_entries (tenant_id, actor_type, actor_id);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_target
  ON ledger_entries (tenant_id, target_type, target_id);

CREATE TABLE IF NOT EXISTS price_snapshots (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id               INTEGER NOT NULL DEFAULT 1,
  reference_type          TEXT NOT NULL,
  reference_id            INTEGER,
  package_id              INTEGER,
  retail_price_minor      INTEGER NOT NULL DEFAULT 0,
  wholesale_price_minor   INTEGER NOT NULL DEFAULT 0,
  effective_price_minor   INTEGER NOT NULL DEFAULT 0,
  discount_amount_minor   INTEGER NOT NULL DEFAULT 0,
  currency                TEXT NOT NULL DEFAULT 'JOD',
  captured_at             TEXT NOT NULL,
  captured_by_type        TEXT NOT NULL DEFAULT '',
  captured_by_id          INTEGER,
  metadata_json           TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_price_snapshots_reference
  ON price_snapshots (tenant_id, reference_type, reference_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_price_snapshots_package
  ON price_snapshots (tenant_id, package_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_price_snapshots_date
  ON price_snapshots (tenant_id, captured_at);

CREATE TABLE IF NOT EXISTS business_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  category        TEXT NOT NULL,
  severity        TEXT NOT NULL DEFAULT 'info',
  actor_type      TEXT NOT NULL DEFAULT '',
  actor_id        INTEGER,
  target_type     TEXT NOT NULL DEFAULT '',
  target_id       INTEGER,
  event_key       TEXT NOT NULL,
  message         TEXT NOT NULL DEFAULT '',
  metadata_json   TEXT NOT NULL DEFAULT '{}',
  correlation_id  TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  CHECK (category IN (
    'manager', 'subscriber', 'card', 'financial', 'system', 'security',
    'radius', 'notification'
  )),
  CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical'))
);

CREATE INDEX IF NOT EXISTS ix_business_events_category_date
  ON business_events (tenant_id, category, id DESC);
CREATE INDEX IF NOT EXISTS ix_business_events_severity_date
  ON business_events (tenant_id, severity, id DESC);
CREATE INDEX IF NOT EXISTS ix_business_events_actor
  ON business_events (tenant_id, actor_type, actor_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_business_events_target
  ON business_events (tenant_id, target_type, target_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_business_events_correlation
  ON business_events (tenant_id, correlation_id);

CREATE TABLE IF NOT EXISTS revenue_records (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                 INTEGER NOT NULL DEFAULT 1,
  source_type               TEXT NOT NULL,
  source_id                 INTEGER,
  price_snapshot_id         INTEGER,
  original_price_minor      INTEGER NOT NULL DEFAULT 0,
  retail_price_minor        INTEGER NOT NULL DEFAULT 0,
  wholesale_cost_minor      INTEGER NOT NULL DEFAULT 0,
  collected_amount_minor    INTEGER NOT NULL DEFAULT 0,
  debt_amount_minor         INTEGER NOT NULL DEFAULT 0,
  discount_amount_minor     INTEGER NOT NULL DEFAULT 0,
  net_profit_minor          INTEGER NOT NULL DEFAULT 0,
  company_share_minor       INTEGER NOT NULL DEFAULT 0,
  distributor_share_minor   INTEGER NOT NULL DEFAULT 0,
  manager_share_minor       INTEGER NOT NULL DEFAULT 0,
  currency                  TEXT NOT NULL DEFAULT 'JOD',
  status                    TEXT NOT NULL DEFAULT 'pending',
  metadata_json             TEXT NOT NULL DEFAULT '{}',
  created_at                TEXT NOT NULL,
  FOREIGN KEY (price_snapshot_id) REFERENCES price_snapshots(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_revenue_records_source
  ON revenue_records (tenant_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_revenue_records_status_date
  ON revenue_records (tenant_id, status, id DESC);

CREATE TABLE IF NOT EXISTS profit_shares (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  beneficiary_type       TEXT NOT NULL,
  beneficiary_id         INTEGER,
  source_type            TEXT NOT NULL,
  source_id              INTEGER,
  gross_amount_minor     INTEGER NOT NULL DEFAULT 0,
  share_amount_minor     INTEGER NOT NULL DEFAULT 0,
  share_percent          REAL NOT NULL DEFAULT 0,
  currency               TEXT NOT NULL DEFAULT 'JOD',
  status                 TEXT NOT NULL DEFAULT 'pending',
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  created_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_profit_shares_beneficiary
  ON profit_shares (tenant_id, beneficiary_type, beneficiary_id, status);
CREATE INDEX IF NOT EXISTS ix_profit_shares_source
  ON profit_shares (tenant_id, source_type, source_id);

CREATE TABLE IF NOT EXISTS archive_snapshots (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  entity_type     TEXT NOT NULL,
  entity_id       INTEGER NOT NULL,
  archive_reason  TEXT NOT NULL DEFAULT '',
  snapshot_json   TEXT NOT NULL DEFAULT '{}',
  archived_by     TEXT NOT NULL DEFAULT '',
  archived_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_archive_snapshots_entity
  ON archive_snapshots (tenant_id, entity_type, entity_id, id DESC);

CREATE TABLE IF NOT EXISTS approval_requests (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id          INTEGER NOT NULL DEFAULT 1,
  request_type       TEXT NOT NULL,
  requester_type     TEXT NOT NULL DEFAULT '',
  requester_id       INTEGER,
  target_type        TEXT NOT NULL DEFAULT '',
  target_id          INTEGER,
  payload_json       TEXT NOT NULL DEFAULT '{}',
  reason             TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL DEFAULT 'pending',
  approved_by_type   TEXT NOT NULL DEFAULT '',
  approved_by_id     INTEGER,
  decided_at         TEXT,
  created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_approval_requests_status
  ON approval_requests (tenant_id, status, id DESC);
CREATE INDEX IF NOT EXISTS ix_approval_requests_requester
  ON approval_requests (tenant_id, requester_type, requester_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_approval_requests_target
  ON approval_requests (tenant_id, target_type, target_id, id DESC);
