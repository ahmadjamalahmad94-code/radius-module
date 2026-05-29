-- Card-user portal login and card-number wallet top-up.

ALTER TABLE card_users
  ADD COLUMN password_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE card_users
  ADD COLUMN password_set_at TEXT;

CREATE INDEX IF NOT EXISTS ix_card_users_tenant_mobile
  ON card_users (tenant_id, mobile);
