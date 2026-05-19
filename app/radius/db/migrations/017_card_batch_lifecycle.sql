-- R1 foundation: additive lifecycle fields for card batches.
-- We do not change current delete behavior yet; these fields allow future
-- archive/restore flows to preserve sensitive card-batch history.

ALTER TABLE card_batches ADD COLUMN deleted_at TEXT;
ALTER TABLE card_batches ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '';
ALTER TABLE card_batches ADD COLUMN delete_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_card_batches_lifecycle
ON card_batches(tenant_id, deleted_at, status);
