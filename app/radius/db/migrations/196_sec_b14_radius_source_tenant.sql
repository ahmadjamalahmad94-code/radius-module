-- 196 — B-14: which tenant owns a RADIUS packet (security fix, 2026-10-05)
--
-- FreeRADIUS used to write the literal tenant_id = 1 on every radacct row it
-- inserted, and /api/v1/internal/auth fell back to tenant 1 for any router it
-- did not recognise. On a server holding more than one network, tenant 1 saw
-- every network's sessions and login attempts.
--
-- The tenant is now derived from the packet's UDP SOURCE address
-- (Packet-Src-IP-Address): FreeRADIUS has already matched that address to a
-- registered client and verified its shared secret, so — unlike the in-packet
-- NAS-IP-Address, which the router writes itself — it cannot be chosen by the
-- router. The source address is the router's tunnel IP when it talks over a
-- management tunnel, so it is matched against the same three columns
-- FreeRADIUS client files are keyed on (freeradius_translator._radius_source_ip):
--     management_remote_address (SSTP/PPTP) · vpn_peer_address (WireGuard) ·
--     address (direct).
--
-- Both FreeRADIUS (deploy/freeradius/mods-enabled/sql) and Flask
-- (app/radius/services/nas_tenant.py) read THESE views, so the rule lives in
-- one place.

-- One row per source address seen on a live, enabled router.
--   tenant_id    = the owning tenant when exactly ONE tenant claims the address,
--                  NULL when two tenants claim it (ambiguous → never guessed).
--   tenant_count = how many distinct tenants claim it.
CREATE VIEW IF NOT EXISTS radius_source_tenant AS
SELECT ip,
       CASE WHEN COUNT(DISTINCT tenant_id) = 1 THEN MIN(tenant_id) END AS tenant_id,
       COUNT(DISTINCT tenant_id) AS tenant_count
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
       )
 GROUP BY ip;

-- The only tenant of a single-network server (NULL as soon as there are two).
-- A packet from an address no router claims is attributed to it: with one
-- tenant there is nobody to leak to, and single-network servers keep exactly
-- their previous behaviour (lab/localhost clients, hand-written clients…).
CREATE VIEW IF NOT EXISTS radius_sole_tenant AS
SELECT CASE WHEN COUNT(*) = 1 THEN MIN(id) END AS tenant_id
  FROM tenants;

-- Quarantine: packets no tenant can be safely given (multi-network server,
-- source address unknown or claimed by two tenants). Deliberately NOT tenant-
-- scoped and NOT readable by any tenant view — for the server operator only.
-- One row per (kind, source, session, user) — updated in place, so a chatty
-- router cannot grow it per packet. No password is ever stored here.
CREATE TABLE IF NOT EXISTS radius_unattributed (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT NOT NULL,                 -- 'acct' | 'auth'
    src_ip            TEXT NOT NULL DEFAULT '',      -- Packet-Src-IP-Address
    nas_ip_attr       TEXT NOT NULL DEFAULT '',      -- in-packet NAS-IP-Address
    username          TEXT NOT NULL DEFAULT '',
    acctsessionid     TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',      -- unknown_nas | ambiguous_nas
    last_status       TEXT NOT NULL DEFAULT '',      -- Start/Interim-Update/Stop/Access-Request
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    packets           INTEGER NOT NULL DEFAULT 1,
    acctsessiontime   INTEGER NOT NULL DEFAULT 0,
    acctinputoctets   INTEGER NOT NULL DEFAULT 0,
    acctoutputoctets  INTEGER NOT NULL DEFAULT 0,
    callingstationid  TEXT NOT NULL DEFAULT '',
    framedipaddress   TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, src_ip, acctsessionid, username)
);
CREATE INDEX IF NOT EXISTS idx_radius_unattributed_seen
    ON radius_unattributed(last_seen);
