-- ════════════════════════════════════════════════════════════════════════
-- 176 — partial index over LIVE radacct rows, matching the exact predicate
--       the code base uses: `acctstoptime IS NULL OR acctstoptime = ''`.
--
-- Stress L01 (2026-09-28): the «اكتف» cap check (provider_grant.
-- count_active_sessions) runs on EVERY login of a capped tenant and filters
-- `tenant_id = ? AND (acctstoptime IS NULL OR acctstoptime='')`. The only
-- usable index was (tenant_id, acctstarttime) over ALL history rows, so each
-- login walked the tenant's whole accounting history (hundreds of thousands
-- of rows on a mature install). SQLite uses a partial index only when the
-- query's WHERE implies the index's WHERE; this one is implied both by the
-- OR form above (identical term — live_sessions, device_limit, reports,
-- policy_reconciler, session_reconciler, provider_grant …) and by the plain
-- `acctstoptime IS NULL` form. It holds only open rows, so it stays tiny and
-- costs almost nothing to maintain (a row leaves it when Stop closes it).
-- ════════════════════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_radacct_live_or_empty
    ON radacct(tenant_id, username)
    WHERE acctstoptime IS NULL OR acctstoptime = '';
