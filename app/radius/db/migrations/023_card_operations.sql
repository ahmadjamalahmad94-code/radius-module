-- Card operations foundation.
-- Additive only. `locked_mac` is admin-controlled and distinct from
-- `used_by_mac`, which is observational and may be captured from first use.

ALTER TABLE cards ADD COLUMN locked_mac TEXT NOT NULL DEFAULT '';
ALTER TABLE cards ADD COLUMN disabled_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE cards ADD COLUMN disabled_at TEXT;
ALTER TABLE cards ADD COLUMN disabled_by TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_cards_locked_mac ON cards(tenant_id, locked_mac);
