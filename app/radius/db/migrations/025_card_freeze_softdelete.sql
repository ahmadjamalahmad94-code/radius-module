-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  Card lifecycle additions:                                           ║
-- ║                                                                      ║
-- ║  1) Freeze remaining time on disable                                 ║
-- ║     When an admin disables a card, the real-world clock keeps        ║
-- ║     ticking and expire_at would silently waste the user's quota.     ║
-- ║     We now snapshot the remaining seconds into                       ║
-- ║     frozen_remaining_seconds, so re-enabling restores the same       ║
-- ║     amount of time from "now" (expire_at = now + frozen).            ║
-- ║                                                                      ║
-- ║  2) Soft delete                                                      ║
-- ║     The Card Checker "حذف" action now moves the card to a recycle    ║
-- ║     bin (deleted_at NOT NULL) instead of dropping the row. The       ║
-- ║     existing /admin/radius/recycle-bin screen can restore or purge.  ║
-- ║                                                                      ║
-- ║  All four columns default to safe zero/empty values, so existing     ║
-- ║  rows are unaffected.                                                ║
-- ╚════════════════════════════════════════════════════════════════════╝

ALTER TABLE cards ADD COLUMN frozen_remaining_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cards ADD COLUMN deleted_at     TEXT;
ALTER TABLE cards ADD COLUMN deleted_by     TEXT NOT NULL DEFAULT '';
ALTER TABLE cards ADD COLUMN delete_reason  TEXT NOT NULL DEFAULT '';

-- Index for the recycle-bin query path (filter by tenant + soft-deleted).
CREATE INDEX IF NOT EXISTS idx_cards_deleted ON cards(tenant_id, deleted_at);
