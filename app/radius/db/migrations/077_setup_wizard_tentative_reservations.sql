-- Setup Wizard — TTL columns for tentative router reservations.
--
-- Problem this fixes:
-- Every wizard run reserves an IP from the VPN pool BEFORE the
-- router actually completes the handshake. When a run fails or is
-- abandoned, the IP stays reserved forever. Over time the pool
-- fills with ghost reservations whose WireGuard keys no longer
-- match anything on the router.
--
-- Solution: every new reservation gets a TTL. If the run doesn't
-- reach vpn_verified (or beyond) within the TTL window, a janitor
-- task releases the IP, deletes the peer file, and marks the row
-- abandoned. Permanent rows (vpn_verified + onwards) are immune.
--
-- Additive only. Existing rows get NULL tentative_expires_at and
-- behave as permanent — that's the safe default for legacy data.

ALTER TABLE router_provisioning_registry
  ADD COLUMN tentative_started_at TEXT NOT NULL DEFAULT '';

ALTER TABLE router_provisioning_registry
  ADD COLUMN tentative_expires_at TEXT NOT NULL DEFAULT '';

ALTER TABLE router_provisioning_registry
  ADD COLUMN tentative_reclaimed_at TEXT NOT NULL DEFAULT '';

ALTER TABLE router_provisioning_registry
  ADD COLUMN tentative_reclaim_reason TEXT NOT NULL DEFAULT '';

-- Index for the janitor's fast lookup of expired rows.
CREATE INDEX IF NOT EXISTS ix_router_provisioning_registry_tentative_expires
  ON router_provisioning_registry (tenant_id, tentative_expires_at)
  WHERE tentative_expires_at <> '';
