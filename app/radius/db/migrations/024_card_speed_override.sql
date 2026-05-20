-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  Per-card speed override (Card Checker → "تغيير سرعة البطاقة")       ║
-- ║                                                                      ║
-- ║  Adds two optional columns on `cards` that, when > 0, override the   ║
-- ║  plan's speed for THIS card only:                                    ║
-- ║                                                                      ║
-- ║    card_speed_down_kbps  — download in kbps (0 = no override)        ║
-- ║    card_speed_up_kbps    — upload   in kbps (0 = no override)        ║
-- ║                                                                      ║
-- ║  Both must be > 0 for the override to apply; setting either back     ║
-- ║  to 0 reverts that card to the plan/batch defaults. Pricing-side     ║
-- ║  bandwidth_schedules still win over the card override (operator     ║
-- ║  time-of-day rules trump per-card admin overrides).                  ║
-- ║                                                                      ║
-- ║  Consumed by:                                                        ║
-- ║   • policy_engine._card_to_subscriber (HTTP /api/internal/auth path) ║
-- ║   • freeradius_translator.sync_subscriber (native rlm_sql path)      ║
-- ║   • SqliteAdapter.push_session_timeout sister: change_user_rate CoA  ║
-- ╚════════════════════════════════════════════════════════════════════╝

ALTER TABLE cards ADD COLUMN card_speed_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cards ADD COLUMN card_speed_up_kbps   INTEGER NOT NULL DEFAULT 0;
