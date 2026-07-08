-- Reclassify store-provisioned PERMANENT subscribers into temporary CARDS.
--
-- Background: migration 140 ("offer = one product") made an INSTANT store sale
-- provision the buyer's own SUBSCRIBER (user_type='subscriber',
-- created_by='card_marketplace', mk-prefixed username). Those rows polluted
-- «قائمة المشتركين» (/admin/radius/users) as fake permanent subscribers. The
-- store now mints a CARD instead; this migration converts the EXISTING
-- store-provisioned subscribers so they, too, become cards — leaving the
-- subscribers list and appearing in the card interfaces.
--
-- Strategy (backup-safe, additive, idempotent — NO deletes, credentials and
-- radacct sessions untouched): for each store-provisioned subscriber we
--   (1) create a dedicated single-card batch,
--   (2) create a `cards` row reusing the SAME username/password + preserved
--       used/first_used_at/expire_at (so the card == the old login, still
--       authenticates via the policy_engine cards fallback),
--   (3) link the buyer's purchase to that card (card_id / purchase_id),
--   (4) FLIP the subscriber to user_type='card' + tag its card_batch_id, which
--       removes it from «قائمة المشتركين» (the users list excludes
--       user_type='card') while keeping it as the card's mirror row.
-- Only rows with created_by='card_marketplace' are touched — genuine permanent
-- subscribers are never affected. Each step is guarded so a re-run is a no-op.

-- (1) One backfill batch per store-provisioned subscriber (correlate on id via a
--     deterministic batch_code). Exactly one row per subscriber (earliest linked
--     purchase supplies package/price, if any).
INSERT INTO card_batches(
    tenant_id, batch_code, package_name, plan_id, count, generated,
    price_per_card, price_bulk, username_prefix, password_length,
    password_charset, created_by, status, package_id,
    count_from_first_connect, time_value, time_unit, metadata, created_at)
SELECT s.tenant_id,
       'MP-BACKFILL-SUB-' || s.id,
       COALESCE(p.name, 'بطاقة'),
       s.plan_id, 1, 1,
       COALESCE(cup.amount_minor, 0) / 100.0,
       COALESCE(cup.amount_minor, 0) / 100.0,
       'mk', 8, 'digits', 'card_marketplace_backfill', 'active',
       cup.package_id,
       1, 0, 'minutes',
       json_object('source', 'card_marketplace',
                   'electronic', 1,
                   'backfilled_from_subscriber', s.id,
                   'package_id', cup.package_id),
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM subscribers s
LEFT JOIN card_user_purchases cup
       ON cup.id = (SELECT MIN(c2.id) FROM card_user_purchases c2
                    WHERE c2.tenant_id = s.tenant_id AND c2.subscriber_id = s.id)
LEFT JOIN card_marketplace_packages p
       ON p.tenant_id = cup.tenant_id AND p.id = cup.package_id
WHERE s.deleted_at IS NULL
  AND s.user_type = 'subscriber'
  AND s.created_by = 'card_marketplace'
  AND NOT EXISTS (SELECT 1 FROM card_batches b
                  WHERE b.tenant_id = s.tenant_id
                    AND b.batch_code = 'MP-BACKFILL-SUB-' || s.id);

-- (2) The card itself — reuse the subscriber's username/password so the existing
--     login keeps working, and preserve used/first_used_at/expire_at so remaining
--     time is unchanged. Guarded by username uniqueness (idx_cards_username).
INSERT INTO cards(
    tenant_id, batch_id, username, password, plan_id,
    used, first_used_at, expire_at, created_at)
SELECT b.tenant_id, b.id, s.username, s.password, s.plan_id,
       CASE WHEN s.first_login_at IS NOT NULL AND s.first_login_at <> ''
            THEN 1 ELSE 0 END,
       s.first_login_at, s.expire_at,
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM card_batches b
JOIN subscribers s
  ON s.tenant_id = b.tenant_id
 AND s.id = CAST(json_extract(b.metadata, '$.backfilled_from_subscriber') AS INTEGER)
WHERE b.created_by = 'card_marketplace_backfill'
  AND s.user_type = 'subscriber'
  AND NOT EXISTS (SELECT 1 FROM cards c
                  WHERE c.tenant_id = b.tenant_id AND c.username = s.username);

-- (3a) Link each buyer's purchase to its new card.
UPDATE card_user_purchases
SET card_id = (
    SELECT c.id FROM cards c
    JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
    WHERE b.created_by = 'card_marketplace_backfill'
      AND b.tenant_id = card_user_purchases.tenant_id
      AND CAST(json_extract(b.metadata, '$.backfilled_from_subscriber') AS INTEGER)
          = card_user_purchases.subscriber_id
    LIMIT 1)
WHERE subscriber_id IS NOT NULL
  AND card_id IS NULL
  AND EXISTS (
    SELECT 1 FROM card_batches b
    WHERE b.created_by = 'card_marketplace_backfill'
      AND b.tenant_id = card_user_purchases.tenant_id
      AND CAST(json_extract(b.metadata, '$.backfilled_from_subscriber') AS INTEGER)
          = card_user_purchases.subscriber_id);

-- (3b) Back-link the card to its purchase (mirrors the live purchase path).
UPDATE cards
SET purchase_id = (
    SELECT cup.id FROM card_user_purchases cup
    WHERE cup.tenant_id = cards.tenant_id AND cup.card_id = cards.id
    LIMIT 1)
WHERE purchase_id IS NULL
  AND batch_id IN (SELECT id FROM card_batches
                   WHERE created_by = 'card_marketplace_backfill');

-- (4) Flip the subscriber into the card's mirror row: user_type='card' removes
--     it from «قائمة المشتركين»; card_batch_id ties it to its batch. Only rows
--     that actually got a card are flipped.
UPDATE subscribers
SET user_type = 'card',
    card_batch_id = (
        SELECT b.id FROM card_batches b
        WHERE b.created_by = 'card_marketplace_backfill'
          AND b.tenant_id = subscribers.tenant_id
          AND CAST(json_extract(b.metadata, '$.backfilled_from_subscriber') AS INTEGER)
              = subscribers.id
        LIMIT 1),
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE user_type = 'subscriber'
  AND created_by = 'card_marketplace'
  AND EXISTS (SELECT 1 FROM cards c
              WHERE c.tenant_id = subscribers.tenant_id
                AND c.username = subscribers.username);
