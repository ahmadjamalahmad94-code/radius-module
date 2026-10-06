-- 197 — B-14 follow-up: server-local RADIUS sources (security fix, 2026-10-06)
--
-- Migration 196 attributes a RADIUS packet to the tenant whose live router
-- claims its source address (Packet-Src-IP-Address). Two gaps showed up when
-- the pre-deploy detection ran on a multi-network server:
--
--   1. The panel host's own NAS — accel-ppp, which sources RADIUS from its
--      gateway address on the host (e.g. 10.50.0.1) — and anything talking
--      from 127.0.0.1 are claimed by no router row, so on a multi-network
--      server all of them were rejected / quarantined.
--   2. Any tenant could add a "router" whose address is 127.0.0.1 or the
--      accel gateway and silently receive every session from it.
--
-- Server-local sources are therefore attributed ONLY through this explicit,
-- operator-only registry (written by tools/radius_local_nas.py, never by a
-- tenant-facing route):
--
--   purpose 'nas'   — a local NAS that serves ONE tenant → that tenant.
--   purpose 'mgmt'  — a local NAS that carries only router management tunnels
--                     (rtr-*) → no tenant: accounting goes to the operator
--                     quarantine as 'local_mgmt', logins are rejected.
--   purpose 'probe' — a health probe / smoke test → nothing is written at all
--                     (no session, no quarantine, no login log, no charge);
--                     accounting is still ACKed, logins get a Reject.
--
-- A registered address is never guessed: it is never given the "only tenant"
-- fallback, so a 'nas' row whose tenant no longer exists fails closed.
-- A router row of any tenant can never claim a loopback address
-- (127.0.0.0/8, ::1, 0.0.0.0) nor a registered local address.
-- Unknown, unregistered sources keep the 196 rule (only tenant on a
-- single-network server; quarantine on a multi-network server).

CREATE TABLE IF NOT EXISTS radius_local_nas (
    ip          TEXT PRIMARY KEY,                       -- Packet-Src-IP-Address
    purpose     TEXT NOT NULL CHECK (purpose IN ('nas', 'mgmt', 'probe')),
    tenant_id   INTEGER,                                -- only for purpose 'nas'
    service     TEXT NOT NULL DEFAULT '',               -- accel-ppp, healthcheck…
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((purpose = 'nas') = (tenant_id IS NOT NULL))
);

DROP VIEW IF EXISTS radius_source_tenant;

-- One row per source address with an owner rule.
--   tenant_id     = the owning tenant, NULL when the address must not be
--                   attributed (two tenants claim it, or a local mgmt / probe
--                   source, or a local 'nas' whose tenant is gone).
--   tenant_count  = how many tenants claim it (1 for a live local 'nas').
--   local_purpose = the registry purpose, NULL for router-claimed addresses.
CREATE VIEW radius_source_tenant AS
SELECT l.ip,
       CASE WHEN l.purpose = 'nas' THEN t.id END AS tenant_id,
       CASE WHEN l.purpose = 'nas' AND t.id IS NOT NULL THEN 1 ELSE 0 END AS tenant_count,
       l.purpose AS local_purpose
  FROM radius_local_nas l
  LEFT JOIN tenants t ON t.id = l.tenant_id
UNION ALL
SELECT ip,
       CASE WHEN COUNT(DISTINCT tenant_id) = 1 THEN MIN(tenant_id) END,
       COUNT(DISTINCT tenant_id),
       NULL
  FROM (
        SELECT CASE WHEN TRIM(management_remote_address) LIKE '%/32'
                    THEN SUBSTR(TRIM(management_remote_address), 1,
                                LENGTH(TRIM(management_remote_address)) - 3)
                    ELSE TRIM(management_remote_address) END AS ip,
               tenant_id
          FROM nas_devices
         WHERE enabled = 1 AND deleted_at IS NULL
           AND TRIM(COALESCE(management_remote_address, '')) <> ''
        UNION ALL
        SELECT CASE WHEN TRIM(vpn_peer_address) LIKE '%/32'
                    THEN SUBSTR(TRIM(vpn_peer_address), 1,
                                LENGTH(TRIM(vpn_peer_address)) - 3)
                    ELSE TRIM(vpn_peer_address) END,
               tenant_id
          FROM nas_devices
         WHERE enabled = 1 AND deleted_at IS NULL
           AND TRIM(COALESCE(vpn_peer_address, '')) <> ''
        UNION ALL
        SELECT TRIM(address), tenant_id
          FROM nas_devices
         WHERE enabled = 1 AND deleted_at IS NULL
           AND TRIM(COALESCE(address, '')) <> ''
       ) claims
 WHERE ip NOT LIKE '127.%'
   AND ip NOT IN ('::1', '0.0.0.0', 'localhost', '')
   AND ip NOT IN (SELECT ip FROM radius_local_nas)
 GROUP BY ip;
