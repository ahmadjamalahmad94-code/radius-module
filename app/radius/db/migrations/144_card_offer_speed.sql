-- Card OFFERS — direct per-offer SPEED (Mikrotik-Rate-Limit) override.
--
-- The owner considers speed an essential commercial term of an offer (not just
-- of the linked plan). These two columns carry a rate-limit, in kbps, that is
-- stamped onto EVERY card generated from the offer as a per-card speed override
-- (cards.card_speed_down_kbps / card_speed_up_kbps, migration 024). The policy
-- engine + freeradius_translator already ENFORCE that per-card override at auth,
-- emitting Mikrotik-Rate-Limit = "<up>k/<down>k".
--
-- Semantics (both columns, in kbps):
--   * 0 / 0          → no offer speed; the generated cards inherit the plan's
--                      own rate-limit (if a plan is chosen), exactly as before.
--   * both > 0       → OFFER SPEED WINS. Every generated card gets this exact
--                      rate-limit, overriding the plan's speed.
-- A mixed 0 + nonzero pair is rejected at the service layer because the per-card
-- override only takes effect when BOTH down AND up are > 0 (policy_engine rule).
--
-- Additive only; no data migration. Existing offers default to 0/0 (plan speed).

ALTER TABLE card_offers ADD COLUMN speed_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_offers ADD COLUMN speed_up_kbps   INTEGER NOT NULL DEFAULT 0;
