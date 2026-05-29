-- Recharge Cards (بطاقات الشحن المسبق) — additive schema.
--
-- New family of cards that exist purely to top-up a card-user's
-- wallet on the customer portal. Each card carries its own monetary
-- value so a single batch can mix denominations (e.g. 100 cards of
-- 5, 50 cards of 10, 20 cards of 20).
--
-- Design:
--   * recharge_only=1 on the batch row partitions the recharge
--     batches from the regular cards listing AND the print-only
--     section. The three sections never mix.
--   * wallet_value on each card row carries the redemption amount.
--     The customer-portal redeem_card_to_wallet service prefers
--     this over batch.price_per_card so multi-denom batches work
--     naturally.
--   * Defence-in-depth: both flags live on both batch AND card so
--     no single mis-write can leak a recharge card into auth.
--
-- The print-only section uses similar shape (084_print_only_cards
-- migration); this is intentional — the operator sees a familiar
-- structure across the three card families.

ALTER TABLE card_batches ADD COLUMN recharge_only INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cards         ADD COLUMN recharge_only INTEGER NOT NULL DEFAULT 0;

-- Per-card wallet value. NULL means "fall back to the batch's
-- price_per_card" so existing rows behave exactly as before.
ALTER TABLE cards ADD COLUMN wallet_value REAL;

-- Composite indexes scoped by tenant — keep the list query path
-- fast for both batches and the cards-of-batch detail view.
CREATE INDEX IF NOT EXISTS idx_card_batches_recharge_only
  ON card_batches (tenant_id, recharge_only, status);

CREATE INDEX IF NOT EXISTS idx_cards_recharge_only
  ON cards (tenant_id, recharge_only, batch_id);
