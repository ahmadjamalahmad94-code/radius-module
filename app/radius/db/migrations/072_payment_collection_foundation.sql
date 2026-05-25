-- Payment Collection Center foundation for customer-network payments.
-- Manual Wallet is the first supported provider. Jawwal Pay rows are stored
-- only as future-safe contracts; unsigned webhooks must not confirm payments.

CREATE TABLE IF NOT EXISTS tenant_payment_settings (
  id                                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                           INTEGER NOT NULL,
  provider                            TEXT NOT NULL DEFAULT 'manual_wallet',
  enabled                             INTEGER NOT NULL DEFAULT 0,
  wallet_number                       TEXT NOT NULL DEFAULT '',
  wallet_owner_name                   TEXT NOT NULL DEFAULT '',
  currency                            TEXT NOT NULL DEFAULT 'ILS',
  confirmation_mode                   TEXT NOT NULL DEFAULT 'manual',
  auto_apply                          INTEGER NOT NULL DEFAULT 0,
  allow_cards                         INTEGER NOT NULL DEFAULT 1,
  allow_monthly_subscriptions         INTEGER NOT NULL DEFAULT 1,
  allow_distributor_payments          INTEGER NOT NULL DEFAULT 1,
  min_amount                          REAL,
  max_amount                          REAL,
  payment_request_ttl_minutes         INTEGER DEFAULT 1440,
  created_at                          TEXT NOT NULL,
  updated_at                          TEXT NOT NULL,
  CHECK(provider IN ('manual_wallet', 'jawwal_pay')),
  CHECK(enabled IN (0, 1)),
  CHECK(confirmation_mode IN ('manual', 'api')),
  CHECK(auto_apply IN (0, 1)),
  CHECK(allow_cards IN (0, 1)),
  CHECK(allow_monthly_subscriptions IN (0, 1)),
  CHECK(allow_distributor_payments IN (0, 1)),
  CHECK(min_amount IS NULL OR min_amount > 0),
  CHECK(max_amount IS NULL OR max_amount > 0),
  CHECK(payment_request_ttl_minutes IS NULL OR payment_request_ttl_minutes > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_payment_settings_tenant
  ON tenant_payment_settings (tenant_id);

CREATE TABLE IF NOT EXISTS payment_requests (
  id                          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                   INTEGER NOT NULL,
  payer_type                  TEXT NOT NULL,
  payer_id                    INTEGER,
  purpose                     TEXT NOT NULL,
  amount                      REAL NOT NULL,
  currency                    TEXT NOT NULL,
  provider                    TEXT NOT NULL,
  receiver_wallet             TEXT NOT NULL DEFAULT '',
  reference_code              TEXT NOT NULL,
  status                      TEXT NOT NULL DEFAULT 'pending',
  expires_at                  TEXT,
  created_by                  INTEGER,
  created_at                  TEXT NOT NULL,
  updated_at                  TEXT NOT NULL,
  CHECK(amount > 0),
  CHECK(provider IN ('manual_wallet', 'jawwal_pay')),
  CHECK(purpose IN (
    'card_purchase',
    'monthly_subscription',
    'subscriber_renewal',
    'quota_topup',
    'time_extension',
    'distributor_payment',
    'loan_settlement'
  )),
  CHECK(status IN (
    'pending',
    'proof_submitted',
    'under_review',
    'paid',
    'rejected',
    'expired',
    'cancelled',
    'failed'
  ))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_requests_tenant_reference
  ON payment_requests (tenant_id, reference_code);

CREATE INDEX IF NOT EXISTS ix_payment_requests_tenant_status
  ON payment_requests (tenant_id, status, id DESC);

CREATE INDEX IF NOT EXISTS ix_payment_requests_tenant_purpose
  ON payment_requests (tenant_id, purpose, id DESC);

CREATE TABLE IF NOT EXISTS payment_proofs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_request_id  INTEGER NOT NULL,
  proof_type          TEXT NOT NULL DEFAULT 'manual_reference',
  reference_number    TEXT,
  image_path          TEXT,
  note                TEXT,
  submitted_at        TEXT NOT NULL,
  reviewed_by         INTEGER,
  reviewed_at         TEXT,
  review_status       TEXT,
  review_note         TEXT,
  CHECK(proof_type IN ('manual_reference', 'image', 'note')),
  CHECK(review_status IS NULL OR review_status IN ('approved', 'rejected')),
  FOREIGN KEY(payment_request_id) REFERENCES payment_requests(id)
);

CREATE INDEX IF NOT EXISTS ix_payment_proofs_request
  ON payment_proofs (payment_request_id, id DESC);

CREATE TABLE IF NOT EXISTS payment_collection_transactions (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_request_id       INTEGER NOT NULL,
  provider_transaction_id  TEXT,
  amount                   REAL NOT NULL,
  currency                 TEXT NOT NULL,
  status                   TEXT NOT NULL,
  raw_payload              TEXT,
  verified_at              TEXT,
  created_at               TEXT NOT NULL,
  CHECK(amount > 0),
  CHECK(status IN ('pending', 'verified_manual', 'paid_manual', 'verified_api', 'failed')),
  FOREIGN KEY(payment_request_id) REFERENCES payment_requests(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_collection_transactions_provider_id
  ON payment_collection_transactions (provider_transaction_id)
  WHERE provider_transaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_payment_collection_transactions_request
  ON payment_collection_transactions (payment_request_id, id DESC);

CREATE TABLE IF NOT EXISTS payment_webhook_events (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  provider            TEXT NOT NULL,
  event_id            TEXT,
  payment_request_id  INTEGER,
  payload             TEXT NOT NULL DEFAULT '{}',
  signature_valid     INTEGER,
  processed           INTEGER NOT NULL DEFAULT 0,
  processed_at        TEXT,
  created_at          TEXT NOT NULL,
  CHECK(provider IN ('manual_wallet', 'jawwal_pay')),
  CHECK(signature_valid IS NULL OR signature_valid IN (0, 1)),
  CHECK(processed IN (0, 1)),
  FOREIGN KEY(payment_request_id) REFERENCES payment_requests(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_webhook_events_provider_event
  ON payment_webhook_events (provider, event_id)
  WHERE event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_payment_webhook_events_request
  ON payment_webhook_events (payment_request_id, id DESC);
