-- Electronic-cards: sale modes (instant vs inventory) + per-offer purchases file.
--
-- Additive only. Existing offers default to sale_mode='instant' (today's
-- behaviour: a real card is minted on every purchase), so nothing changes on
-- deploy and the instant path stays byte-for-byte the same.
--
-- 'inventory' mode pre-generates OR imports (Excel/CSV/PDF) a fixed stock pool
-- into a batch linked to the offer (card_batches.package_id). Each sale then
-- atomically claims the next free card (cards.purchase_id) instead of minting,
-- and bumps O(1) counters on the offer:
--     remaining = inventory_total - inventory_sold.
--
-- The per-offer "purchases file" and the global recent-purchases panel both
-- read card_user_purchases, grouped/sorted by offer via a new index.
--
-- All columns are tenant-scoped via the existing tenant_id on each table.

-- 1) Offer-level sale mode + stock counters.
ALTER TABLE card_marketplace_packages ADD COLUMN sale_mode TEXT NOT NULL DEFAULT 'instant';
ALTER TABLE card_marketplace_packages ADD COLUMN inventory_total INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_marketplace_packages ADD COLUMN inventory_sold INTEGER NOT NULL DEFAULT 0;

-- 2) Link a generated/imported stock batch back to its offer.
ALTER TABLE card_batches ADD COLUMN package_id INTEGER;
CREATE INDEX IF NOT EXISTS ix_card_batches_package
    ON card_batches (tenant_id, package_id);

-- 3) Mark an inventory card as claimed by a specific purchase (atomic sell).
--    NULL = still in stock. A card can back at most one purchase.
ALTER TABLE cards ADD COLUMN purchase_id INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS ux_cards_purchase
    ON cards (tenant_id, purchase_id) WHERE purchase_id IS NOT NULL;

-- 4) Fast per-offer purchases file + global recent-purchases panel.
CREATE INDEX IF NOT EXISTS ix_card_user_purchases_package
    ON card_user_purchases (tenant_id, package_id, id DESC);
