-- Marketplace "offer = one product" (Option A).
--
-- Additive only. Stops minting a NEW cards+card_batches row on every INSTANT
-- sale. Instead a purchase provisions the buyer's OWN authenticatable
-- subscriber (their own connection / session / quota / expiry, driven by the
-- offer's plan) and the credential is stored on the purchase row for display.
--
-- INVENTORY mode is unchanged: a sale still atomically claims a pre-made stock
-- card from the offer's pool (cards.purchase_id), so card_id stays populated
-- there. INSTANT sales now have card_id NULL + subscriber_id/cred_* set.
--
-- No data migration: the owner is resetting the (test) DB. These columns simply
-- default to NULL on any pre-existing rows; nothing is rewritten or destroyed.

-- Per-buyer credential + the subscriber that backs it (instant mode).
ALTER TABLE card_user_purchases ADD COLUMN cred_username TEXT;
ALTER TABLE card_user_purchases ADD COLUMN cred_password TEXT;
ALTER TABLE card_user_purchases ADD COLUMN subscriber_id INTEGER;

-- Look up an offer's purchases by their backing subscriber (compensation/refund
-- + the buyer-360 view).
CREATE INDEX IF NOT EXISTS ix_card_user_purchases_subscriber
    ON card_user_purchases (tenant_id, subscriber_id);
