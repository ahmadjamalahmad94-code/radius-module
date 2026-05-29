-- Hotspot Electronic Cards Portal backend foundation.
-- MikroTik remains UI-only; all wallet, pricing, purchase, and issuance logic
-- is owned by radius-module APIs.

CREATE TABLE IF NOT EXISTS hotspot_portal_tokens (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  token_hash      TEXT NOT NULL UNIQUE,
  owner_type      TEXT NOT NULL,
  owner_id        INTEGER NOT NULL,
  username        TEXT NOT NULL DEFAULT '',
  expires_at      TEXT NOT NULL,
  revoked_at      TEXT,
  created_at      TEXT NOT NULL,
  last_seen_at    TEXT,
  CHECK (owner_type IN ('subscriber', 'card_user'))
);

CREATE INDEX IF NOT EXISTS ix_hotspot_portal_tokens_owner
  ON hotspot_portal_tokens (tenant_id, owner_type, owner_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_hotspot_portal_tokens_expiry
  ON hotspot_portal_tokens (tenant_id, expires_at);

CREATE TABLE IF NOT EXISTS hotspot_card_purchases (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  owner_type             TEXT NOT NULL,
  owner_id               INTEGER NOT NULL,
  package_id             INTEGER NOT NULL,
  card_id                INTEGER,
  wallet_id              INTEGER,
  wallet_transaction_id  INTEGER,
  ledger_entry_id        INTEGER,
  amount_minor           INTEGER NOT NULL DEFAULT 0,
  currency               TEXT NOT NULL DEFAULT 'ILS',
  client_request_id      TEXT NOT NULL DEFAULT '',
  status                 TEXT NOT NULL DEFAULT 'completed',
  metadata_json          TEXT NOT NULL DEFAULT '{}',
  sms_sent_at            TEXT,
  created_at             TEXT NOT NULL,
  FOREIGN KEY (package_id) REFERENCES card_marketplace_packages(id) ON DELETE RESTRICT,
  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE SET NULL,
  FOREIGN KEY (wallet_id) REFERENCES wallets(id) ON DELETE SET NULL,
  FOREIGN KEY (wallet_transaction_id) REFERENCES wallet_transactions(id) ON DELETE SET NULL,
  FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id) ON DELETE SET NULL,
  CHECK (owner_type IN ('subscriber', 'card_user')),
  CHECK (status IN ('completed', 'failed', 'voided'))
);

CREATE INDEX IF NOT EXISTS ix_hotspot_card_purchases_owner
  ON hotspot_card_purchases (tenant_id, owner_type, owner_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_hotspot_card_purchases_card
  ON hotspot_card_purchases (tenant_id, card_id);
CREATE INDEX IF NOT EXISTS ix_hotspot_card_purchases_package
  ON hotspot_card_purchases (tenant_id, package_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_hotspot_card_purchases_request
  ON hotspot_card_purchases (tenant_id, owner_type, owner_id, client_request_id)
  WHERE client_request_id IS NOT NULL AND client_request_id <> '';

CREATE TABLE IF NOT EXISTS hotspot_card_sms_attempts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id      INTEGER NOT NULL DEFAULT 1,
  purchase_id    INTEGER NOT NULL,
  owner_type     TEXT NOT NULL,
  owner_id       INTEGER NOT NULL,
  phone          TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'failed',
  error_code     TEXT NOT NULL DEFAULT '',
  provider_msg   TEXT NOT NULL DEFAULT '',
  metadata_json  TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL,
  FOREIGN KEY (purchase_id) REFERENCES hotspot_card_purchases(id) ON DELETE RESTRICT,
  CHECK (owner_type IN ('subscriber', 'card_user')),
  CHECK (status IN ('queued', 'sent', 'failed'))
);

CREATE INDEX IF NOT EXISTS ix_hotspot_card_sms_attempts_purchase
  ON hotspot_card_sms_attempts (tenant_id, purchase_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_hotspot_card_sms_attempts_owner
  ON hotspot_card_sms_attempts (tenant_id, owner_type, owner_id, id DESC);
