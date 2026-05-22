-- N3: drop the legacy mikrotik_configs table.
--
-- Phase K introduced nas_devices as the canonical MikroTik
-- table. Phase L added the wizard + Operations Center that read
-- and write nas_devices exclusively. Phase N1 (commit acc0323)
-- removed the table from the diagnostics surface. Phase N2
-- (commit 26a98ce) turned the /admin/radius/mt CRUD into 410
-- Gone, so nothing writes to it any more.
--
-- This migration is the final step: copy any still-live rows
-- into nas_devices, then drop the table. After it runs, the
-- table is gone for good — see docs/radius/POSTMORTEM_PHASE_K_L_M.md
-- (issue #14) for the full backstory.
--
-- Idempotency: wrapped in IF EXISTS / WHERE NOT EXISTS so a
-- re-run on a DB without the table is a no-op.

-- 1) Best-effort copy: any mikrotik_configs row whose host isn't
--    already in nas_devices gets promoted. The new row is marked
--    enabled=0 by default — operators must confirm via the
--    devices/<id>/edit page before HobeRadius dials it. This is
--    deliberate: legacy rows often had stale credentials.
INSERT INTO nas_devices
    (tenant_id, name, address, secret, vendor, nas_type, enabled,
     api_port, api_user, api_password, api_use_tls,
     description, created_at)
SELECT
    mc.tenant_id,
    mc.name,
    mc.host,
    'migrated-from-legacy-edit-me' AS secret,
    'mikrotik' AS vendor,
    'hotspot'  AS nas_type,
    0          AS enabled,
    mc.port,
    mc.username,
    mc.password,
    mc.use_tls,
    'Migrated from mikrotik_configs (N3) — review credentials before enabling.' AS description,
    COALESCE(mc.created_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')) AS created_at
FROM mikrotik_configs AS mc
WHERE NOT EXISTS (
    SELECT 1 FROM nas_devices nd
    WHERE nd.tenant_id = mc.tenant_id
      AND nd.address   = mc.host
);

-- 2) Drop the legacy table.
DROP TABLE IF EXISTS mikrotik_configs;
