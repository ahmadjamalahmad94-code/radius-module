-- B-14 — READ-ONLY detection of RADIUS rows stored under the wrong tenant.
-- Safe to run on a live server:  sqlite3 -readonly /data/hoberadius.db < sec_b14_detect.sql
-- Selects no password column. Changes nothing. Works before AND after migration
-- 196 (it does not use the new views). See docs/security/SEC_FIX_B14.md.

.headers on
.mode column

-- D1  Is this a multi-network server at all?  tenants_total = 1 → nothing to fix.
SELECT COUNT(*) AS tenants_total,
       SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS tenants_active,
       MIN(id) AS lowest_tenant_id
  FROM tenants;

-- D2  Source address → owning tenant, the rule of migration 196
--     (live, enabled routers, tunnel IPs first, /32 stripped).
WITH addr AS (
    SELECT CASE WHEN TRIM(management_remote_address) LIKE '%/32'
                THEN SUBSTR(TRIM(management_remote_address), 1, LENGTH(TRIM(management_remote_address)) - 3)
                ELSE TRIM(management_remote_address) END AS ip, tenant_id, created_at
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL AND TRIM(COALESCE(management_remote_address,'')) <> ''
    UNION ALL
    SELECT CASE WHEN TRIM(vpn_peer_address) LIKE '%/32'
                THEN SUBSTR(TRIM(vpn_peer_address), 1, LENGTH(TRIM(vpn_peer_address)) - 3)
                ELSE TRIM(vpn_peer_address) END, tenant_id, created_at
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL AND TRIM(COALESCE(vpn_peer_address,'')) <> ''
    UNION ALL
    SELECT TRIM(address), tenant_id, created_at
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL AND TRIM(COALESCE(address,'')) <> ''
)
SELECT ip, COUNT(DISTINCT tenant_id) AS tenants_claiming,
       GROUP_CONCAT(DISTINCT tenant_id) AS tenant_ids
  FROM addr GROUP BY ip ORDER BY tenants_claiming DESC, ip;

-- D3  radacct: stored tenant vs owning tenant of nasipaddress (= packet source).
--     owner = -1: no live router claims the address, owner = -2: two tenants claim it.
WITH addr AS (
    SELECT CASE WHEN TRIM(management_remote_address) LIKE '%/32'
                THEN SUBSTR(TRIM(management_remote_address), 1, LENGTH(TRIM(management_remote_address)) - 3)
                ELSE TRIM(management_remote_address) END AS ip, tenant_id
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL AND TRIM(COALESCE(management_remote_address,'')) <> ''
    UNION ALL
    SELECT CASE WHEN TRIM(vpn_peer_address) LIKE '%/32'
                THEN SUBSTR(TRIM(vpn_peer_address), 1, LENGTH(TRIM(vpn_peer_address)) - 3)
                ELSE TRIM(vpn_peer_address) END, tenant_id
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL AND TRIM(COALESCE(vpn_peer_address,'')) <> ''
    UNION ALL
    SELECT TRIM(address), tenant_id
      FROM nas_devices WHERE enabled = 1 AND deleted_at IS NULL AND TRIM(COALESCE(address,'')) <> ''
), owner AS (
    SELECT ip, CASE WHEN COUNT(DISTINCT tenant_id) = 1 THEN MIN(tenant_id) ELSE -2 END AS tenant_id
      FROM addr GROUP BY ip
)
SELECT r.tenant_id AS stored_tenant,
       COALESCE(o.tenant_id, -1) AS owner_tenant,
       SUM(CASE WHEN r.acctstoptime IS NULL THEN 1 ELSE 0 END) AS open_rows,
       COUNT(*) AS rows_,
       MIN(r.acctstarttime) AS oldest, MAX(r.acctstarttime) AS newest
  FROM radacct r LEFT JOIN owner o ON o.ip = r.nasipaddress
 GROUP BY stored_tenant, owner_tenant
 ORDER BY rows_ DESC;
--  Rows with stored_tenant <> owner_tenant AND owner_tenant > 0 are mis-attributed
--  (candidates for tools/sec_b14_backfill.py). owner_tenant -1 / -2 rows stay put.

-- D4  radpostauth (login attempts). `nas` is the in-packet NAS-IP-Address (no
--     source address was stored), so this is indicative only. NO `pass` column.
SELECT p.tenant_id AS stored_tenant,
       CASE WHEN EXISTS (SELECT 1 FROM nas_devices n
                          WHERE n.tenant_id = p.tenant_id AND n.deleted_at IS NULL
                            AND p.nas IN (n.address, n.vpn_peer_address, n.management_remote_address))
            THEN 'nas_of_same_tenant' ELSE 'nas_not_of_this_tenant' END AS nas_match,
       COUNT(*) AS rows_, MIN(authdate) AS oldest, MAX(authdate) AS newest
  FROM radpostauth p
 GROUP BY stored_tenant, nas_match
 ORDER BY rows_ DESC;

-- D6  PRE-DEPLOY on a multi-network server: RADIUS clients that FreeRADIUS
--     accepts but no live router row claims. After the fix their logins are
--     rejected ("Unknown NAS") and their accounting is quarantined — register
--     them on the right network first. (Legacy SQL `nas` table, also compare
--     the ipaddr of /data/freeradius-clients-wizard/*.conf by hand.)
SELECT n.nasname, n.shortname, n.tenant_id AS legacy_tenant_column
  FROM nas n
 WHERE NOT EXISTS (SELECT 1 FROM nas_devices d
                    WHERE d.enabled = 1 AND d.deleted_at IS NULL
                      AND n.nasname IN (TRIM(d.address), TRIM(d.vpn_peer_address),
                                        TRIM(d.management_remote_address)));
--     And sources seen in the last 7 days that no live router claims:
SELECT r.nasipaddress, COUNT(*) AS sessions_7d
  FROM radacct r
 WHERE r.acctstarttime >= datetime('now', '-7 days')
   AND NOT EXISTS (SELECT 1 FROM nas_devices d
                    WHERE d.enabled = 1 AND d.deleted_at IS NULL
                      AND r.nasipaddress IN (TRIM(d.address), TRIM(d.vpn_peer_address),
                                             TRIM(d.management_remote_address)))
 GROUP BY r.nasipaddress ORDER BY sessions_7d DESC;

-- D5  After the fix: what went to quarantine (table exists only after migration 196).
-- SELECT kind, reason, src_ip, COUNT(*) AS keys, SUM(packets) AS packets, MAX(last_seen) AS last_seen
--   FROM radius_unattributed GROUP BY kind, reason, src_ip ORDER BY packets DESC
