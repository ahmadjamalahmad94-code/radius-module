-- ════════════════════════════════════════════════════════════════════════
-- 180 — NAS name unique among LIVE routers only
--        (was 178 on agent/fix2-network; renumbered at integration)
--
-- Re-test R07 N1 (2026-09-29): idx_nas_unique (tenant_id, name) also covered
-- archived (recycle-bin) rows, so a deleted router's name could never be
-- reused and every attempt was a raw 500 (IntegrityError). The service now
-- checks live names first (409 in Arabic); this partial index keeps the DB
-- guarantee for live rows and frees the names of deleted ones.
-- ════════════════════════════════════════════════════════════════════════
DROP INDEX IF EXISTS idx_nas_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_nas_unique_live
    ON nas_devices(tenant_id, name)
    WHERE deleted_at IS NULL;
