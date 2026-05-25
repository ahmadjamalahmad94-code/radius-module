-- Card users and card marketplace foundation.
-- Additive only; no existing card or RADIUS auth tables are rewritten.

CREATE TABLE IF NOT EXISTS card_users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  display_name    TEXT NOT NULL,
  mobile          TEXT NOT NULL DEFAULT '',
  email           TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'active',
  metadata_json   TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL,
  updated_at      TEXT,
  CHECK (status IN ('active', 'disabled', 'archived'))
);

CREATE INDEX IF NOT EXISTS ix_card_users_tenant_status
  ON card_users (tenant_id, status, id DESC);

CREATE TABLE IF NOT EXISTS card_marketplace_packages (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL DEFAULT 1,
  name              TEXT NOT NULL,
  plan_id           INTEGER NOT NULL,
  duration_minutes  INTEGER NOT NULL DEFAULT 0,
  speed_down_kbps   INTEGER NOT NULL DEFAULT 0,
  speed_up_kbps     INTEGER NOT NULL DEFAULT 0,
  price_minor       INTEGER NOT NULL DEFAULT 0,
  currency          TEXT NOT NULL DEFAULT 'JOD',
  active            INTEGER NOT NULL DEFAULT 1,
  metadata_json     TEXT NOT NULL DEFAULT '{}',
  created_at        TEXT NOT NULL,
  updated_at        TEXT,
  FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE RESTRICT,
  UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS ix_card_marketplace_packages_active
  ON card_marketplace_packages (tenant_id, active, id DESC);

CREATE TABLE IF NOT EXISTS card_user_purchases (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  card_user_id           INTEGER NOT NULL,
  package_id             INTEGER NOT NULL,
  card_id                INTEGER,
  wallet_id              INTEGER,
  wallet_transaction_id  INTEGER,
  ledger_entry_id        INTEGER,
  revenue_record_id      INTEGER,
  amount_minor           INTEGER NOT NULL DEFAULT 0,
  currency               TEXT NOT NULL DEFAULT 'JOD',
  status                 TEXT NOT NULL DEFAULT 'completed',
  delivery_status        TEXT NOT NULL DEFAULT 'event_only',
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  created_at             TEXT NOT NULL,
  FOREIGN KEY (card_user_id) REFERENCES card_users(id) ON DELETE RESTRICT,
  FOREIGN KEY (package_id) REFERENCES card_marketplace_packages(id) ON DELETE RESTRICT,
  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE SET NULL,
  CHECK (status IN ('completed', 'failed', 'voided')),
  CHECK (delivery_status IN ('event_only', 'queued', 'sent', 'failed'))
);

CREATE INDEX IF NOT EXISTS ix_card_user_purchases_user
  ON card_user_purchases (tenant_id, card_user_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_card_user_purchases_card
  ON card_user_purchases (tenant_id, card_id);
