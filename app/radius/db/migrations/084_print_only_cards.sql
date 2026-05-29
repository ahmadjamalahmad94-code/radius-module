-- Print-Only Cards — additive flag on card_batches + cards.
--
-- Purpose: a separate operator-facing section for "printable cards"
-- that exist in the HobeRadius DB purely so the print service can
-- render them onto labels / vouchers. They are NEVER pushed to
-- FreeRADIUS, so any login attempt is rejected by the simple fact
-- that no radcheck/radreply rows exist for them.
--
-- Design:
--   * print_only = 0  → behaves exactly like every other card today.
--   * print_only = 1  → the import pipeline skips RADIUS sync; the
--                       auth-side service skips these rows even when
--                       a future bug accidentally tries to sync them.
--
-- The flag lives on BOTH card_batches AND cards so:
--   1. The batches list can colour the row by batch-level flag with
--      a single column read (no join).
--   2. The cards-of-batch screen can defensively block any
--      "activate this card on RADIUS" action without re-fetching the
--      batch.
--
-- Pricing fields (price_per_card + price_bulk) already exist on
-- card_batches from 058_card_pricing_costing.sql — re-used directly.

ALTER TABLE card_batches ADD COLUMN print_only INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cards         ADD COLUMN print_only INTEGER NOT NULL DEFAULT 0;

-- Partition queries: «list print-only batches» vs «list normal
-- batches». Composite with tenant_id keeps the existing list
-- query path fast.
CREATE INDEX IF NOT EXISTS idx_card_batches_print_only
  ON card_batches (tenant_id, print_only, status);

-- Same idea on cards — the print-only-cards-of-batch view filters
-- by both batch_id and print_only=1 as a defence-in-depth check.
CREATE INDEX IF NOT EXISTS idx_cards_print_only
  ON cards (tenant_id, print_only, batch_id);
