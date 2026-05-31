-- 090_subscriber_custom_price.sql
-- Adds an optional per-subscriber custom price that overrides the plan (offer)
-- price for this specific subscriber. NULL / 0 means "use the plan price".
-- Idempotent: the migration runner skips "duplicate column" if already applied.
ALTER TABLE subscribers ADD COLUMN custom_price REAL;
