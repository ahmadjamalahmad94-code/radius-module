-- ════════════════════════════════════════════════════════════════════════
-- 175 — partial index for OPEN radacct sessions («المتصلون الآن»)
--
-- Stress campaign A08 (2026-09-28): /sessions/online took ~2.7 s at 500 open
-- sessions. Every live-list / counter query filters
-- `tenant_id = ? AND acctstoptime IS NULL` and the only index was
-- (tenant_id, acctstarttime) over ALL history rows, so SQLite walked the whole
-- tenant history to find the few open rows. This partial index holds only the
-- open rows (tiny) and serves the ORDER BY acctstarttime DESC directly.
-- ════════════════════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_radacct_open_by_tenant
    ON radacct(tenant_id, acctstarttime DESC)
    WHERE acctstoptime IS NULL;
CREATE INDEX IF NOT EXISTS idx_radacct_open_by_user
    ON radacct(tenant_id, username)
    WHERE acctstoptime IS NULL;
