-- 163 — soft-delete for marketplace offers (card_marketplace_packages).
--
-- «حذف العرض» removes the offer from the marketplace + management view but
-- keeps every already-minted/sold card valid (cards reference package_id, so
-- we keep the row and only flag it deleted — mirrors the card soft-delete
-- rule). NULL = live; a timestamp = deleted. list_packages / get_package
-- filter `deleted_at IS NULL`.
ALTER TABLE card_marketplace_packages ADD COLUMN deleted_at TEXT DEFAULT NULL;
