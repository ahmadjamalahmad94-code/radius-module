-- NPC remote port mappings (Phase 5R — Remote Tunnel Relay)
--
-- One row per (router, service) for which the VPS exposes a
-- public TCP port that nginx-stream forwards to the router's
-- private/VPN address. Lets the operator reach Winbox / SSH /
-- WebFig / API on the router from outside the network through
-- the VPS public IP.
--
-- Port assignment is monotonic: the allocator scans existing
-- rows and assigns the next free port from a configured range
-- (default 51000..51999). Mappings are stable across restarts;
-- the same router+service keeps the same port unless explicitly
-- released.

CREATE TABLE IF NOT EXISTS npc_remote_port_mappings (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL,
  router_id         INTEGER NOT NULL,
  service           TEXT    NOT NULL,
  public_port       INTEGER NOT NULL,
  upstream_address  TEXT    NOT NULL,
  upstream_port     INTEGER NOT NULL,
  enabled           INTEGER NOT NULL DEFAULT 1,
  created_at        TEXT    NOT NULL,
  updated_at        TEXT    NOT NULL,
  UNIQUE (router_id, service),
  UNIQUE (public_port)
);

CREATE INDEX IF NOT EXISTS idx_npc_remote_port_router
    ON npc_remote_port_mappings(router_id);

CREATE INDEX IF NOT EXISTS idx_npc_remote_port_enabled
    ON npc_remote_port_mappings(enabled);
