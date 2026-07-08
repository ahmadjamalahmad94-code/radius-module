-- Consolidate the per-subscriber store batches into ONE batch PER OFFER.
--
-- Migration 158 minted a SEPARATE single-card batch for every store-provisioned
-- subscriber (batch_code 'MP-BACKFILL-SUB-<id>', created_by
-- 'card_marketplace_backfill', count=1, time_value=0). With thousands of store
-- cards that produced thousands of tiny, time-less batches on
-- /admin/radius/cards/batches. This migration regroups them: all cards of the
-- same offer move into a single shared «<offer> — سوق إلكتروني» batch
-- (batch_code 'MP-OFFER-<package_id>', created_by 'card_marketplace') that
-- carries the offer's from-first-connect time budget, and the emptied
-- single-card batches are removed.
--
-- Grouping key: the OFFER — COALESCE('MP-OFFER-'||package_id,
-- 'MP-OFFER-PLAN-'||plan_id) (package_id is set from the buyer's purchase in 158;
-- the plan-based fallback covers the rare backfill batch with no linked offer).
-- Duration source: the offer's «كم الوقت» —
-- COALESCE(NULLIF(package.duration_minutes,0), plan.duration_minutes, 0) minutes.
--
-- Backup-safe / idempotent / non-destructive to card identity: only card.batch_id
-- (linkage) is rewritten; usernames, passwords, used/first_used_at/expire_at,
-- radacct and login history are untouched, so remaining time and auth are
-- preserved. A shared 'MP-OFFER-<id>' batch created live by a new purchase is
-- reused (NOT EXISTS guard). Re-running is a no-op (backfill batches are gone).

-- (1) Create the shared per-offer store batch for each distinct offer that has
--     backfill batches (reuse a live-created one if it already exists).
INSERT INTO card_batches(
    tenant_id, batch_code, package_name, plan_id, count, generated,
    price_per_card, price_bulk, username_prefix, password_length, password_charset,
    created_by, status, package_id, count_from_first_connect, time_value, time_unit,
    metadata, created_at)
SELECT g.tenant_id,
       g.code,
       COALESCE(p.name, 'بطاقة') || ' — سوق إلكتروني',
       g.plan_id, 0, 0, 0, 0, 'mk', 8, 'digits',
       'card_marketplace', 'active', g.package_id,
       1,
       COALESCE(NULLIF(p.duration_minutes, 0), ap.duration_minutes, 0),
       'minutes',
       json_object('source', 'card_marketplace', 'electronic', 1,
                   'package_id', g.package_id),
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (
    SELECT tenant_id,
           package_id,
           MIN(plan_id) AS plan_id,
           COALESCE('MP-OFFER-' || package_id, 'MP-OFFER-PLAN-' || MIN(plan_id)) AS code
    FROM card_batches
    WHERE created_by = 'card_marketplace_backfill'
    GROUP BY tenant_id, COALESCE('MP-OFFER-' || package_id, 'MP-OFFER-PLAN-' || plan_id)
) g
LEFT JOIN card_marketplace_packages p ON p.tenant_id = g.tenant_id AND p.id = g.package_id
LEFT JOIN access_plans ap ON ap.tenant_id = g.tenant_id AND ap.id = g.plan_id
WHERE NOT EXISTS (SELECT 1 FROM card_batches b
                  WHERE b.tenant_id = g.tenant_id AND b.batch_code = g.code);

-- (2) Move every card out of its single-card backfill batch into the shared
--     per-offer batch (matched by the same grouping key).
UPDATE cards
SET batch_id = (
    SELECT tgt.id FROM card_batches tgt
    WHERE tgt.tenant_id = cards.tenant_id
      AND tgt.batch_code = (
          SELECT COALESCE('MP-OFFER-' || src.package_id, 'MP-OFFER-PLAN-' || src.plan_id)
          FROM card_batches src WHERE src.id = cards.batch_id)
    LIMIT 1)
WHERE batch_id IN (SELECT id FROM card_batches WHERE created_by = 'card_marketplace_backfill')
  AND EXISTS (
    SELECT 1 FROM card_batches tgt
    WHERE tgt.tenant_id = cards.tenant_id
      AND tgt.batch_code = (
          SELECT COALESCE('MP-OFFER-' || src.package_id, 'MP-OFFER-PLAN-' || src.plan_id)
          FROM card_batches src WHERE src.id = cards.batch_id));

-- (3) Repoint the card-mirror subscriber rows (user_type='card') at the shared
--     batch, so card_batch_id stays consistent with the card's new home.
UPDATE subscribers
SET card_batch_id = (
    SELECT c.batch_id FROM cards c
    WHERE c.tenant_id = subscribers.tenant_id AND c.username = subscribers.username
    LIMIT 1)
WHERE user_type = 'card'
  AND card_batch_id IN (SELECT id FROM card_batches WHERE created_by = 'card_marketplace_backfill');

-- (4) Recompute the shared store batches' counters from the actual cards now in
--     them (count = generated = live card count). Idempotent — always exact.
UPDATE card_batches
SET count = (SELECT COUNT(*) FROM cards c
             WHERE c.tenant_id = card_batches.tenant_id AND c.batch_id = card_batches.id
               AND c.deleted_at IS NULL),
    generated = (SELECT COUNT(*) FROM cards c
                 WHERE c.tenant_id = card_batches.tenant_id AND c.batch_id = card_batches.id
                   AND c.deleted_at IS NULL)
WHERE created_by = 'card_marketplace' AND batch_code LIKE 'MP-OFFER-%';

-- (5) Remove the now-empty single-card backfill batches.
DELETE FROM card_batches
WHERE created_by = 'card_marketplace_backfill'
  AND NOT EXISTS (SELECT 1 FROM cards c
                  WHERE c.tenant_id = card_batches.tenant_id AND c.batch_id = card_batches.id);
