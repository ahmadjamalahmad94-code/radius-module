-- Native subscriber portal API sessions.
-- Tokens are stored hashed and scoped to one subscriber only.

CREATE TABLE IF NOT EXISTS customer_portal_tokens (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  token_hash      TEXT NOT NULL UNIQUE,
  owner_type      TEXT NOT NULL DEFAULT 'subscriber',
  owner_id        INTEGER NOT NULL,
  username        TEXT NOT NULL DEFAULT '',
  expires_at      TEXT NOT NULL,
  revoked_at      TEXT,
  created_at      TEXT NOT NULL,
  last_seen_at    TEXT,
  CHECK (owner_type IN ('subscriber'))
);

CREATE INDEX IF NOT EXISTS ix_customer_portal_tokens_owner
  ON customer_portal_tokens (tenant_id, owner_type, owner_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_customer_portal_tokens_expiry
  ON customer_portal_tokens (tenant_id, expires_at);
