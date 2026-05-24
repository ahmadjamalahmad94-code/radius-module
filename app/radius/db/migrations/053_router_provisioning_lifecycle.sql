-- Setup Wizard router provisioning lifecycle and prepared WireGuard peer ledger.
-- Additive only. No live router/server mutation and no plaintext secret material.

ALTER TABLE router_provisioning_registry ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'reserved';
ALTER TABLE router_provisioning_registry ADD COLUMN failure_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE router_provisioning_registry ADD COLUMN lifecycle_updated_at TEXT NOT NULL DEFAULT '';

UPDATE router_provisioning_registry
SET lifecycle_state = CASE
    WHEN status = 'verified' THEN 'fully_onboarded'
    WHEN status = 'generated' THEN 'script_generated'
    WHEN status = 'applied' THEN 'peer_ready'
    WHEN status = 'failed' THEN 'failed'
    WHEN status = 'retired' THEN 'retired'
    ELSE 'reserved'
  END,
  lifecycle_updated_at = COALESCE(NULLIF(updated_at, ''), created_at)
WHERE lifecycle_updated_at = '';

CREATE TABLE IF NOT EXISTS router_lifecycle_events (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  registry_id         INTEGER NOT NULL,
  wizard_run_id       INTEGER,
  from_state          TEXT NOT NULL DEFAULT '',
  to_state            TEXT NOT NULL,
  event_type          TEXT NOT NULL DEFAULT 'transition',
  actor               TEXT NOT NULL DEFAULT 'system',
  reason              TEXT NOT NULL DEFAULT '',
  metadata_json       TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_router_lifecycle_events_registry
  ON router_lifecycle_events (tenant_id, registry_id, id ASC);

CREATE INDEX IF NOT EXISTS ix_router_lifecycle_events_run
  ON router_lifecycle_events (tenant_id, wizard_run_id, id ASC);

CREATE TABLE IF NOT EXISTS prepared_wireguard_peers (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                  INTEGER NOT NULL DEFAULT 1,
  registry_id                INTEGER NOT NULL,
  wizard_run_id              INTEGER,
  peer_name                  TEXT NOT NULL,
  router_vpn_ip              TEXT NOT NULL,
  server_vpn_ip              TEXT NOT NULL,
  router_public_key          TEXT NOT NULL DEFAULT '',
  router_public_key_masked   TEXT NOT NULL DEFAULT '',
  server_public_key          TEXT NOT NULL DEFAULT '',
  server_private_key_ref     TEXT NOT NULL DEFAULT '',
  allowed_ips                TEXT NOT NULL,
  listen_port                INTEGER NOT NULL DEFAULT 51820,
  status                     TEXT NOT NULL DEFAULT 'prepared',
  error_message              TEXT NOT NULL DEFAULT '',
  created_at                 TEXT NOT NULL,
  updated_at                 TEXT NOT NULL,
  retired_at                 TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_prepared_wireguard_peers_registry
  ON prepared_wireguard_peers (tenant_id, registry_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_prepared_wireguard_peers_run
  ON prepared_wireguard_peers (tenant_id, wizard_run_id, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_prepared_wireguard_peers_active_registry
  ON prepared_wireguard_peers (tenant_id, registry_id)
  WHERE status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied');

CREATE UNIQUE INDEX IF NOT EXISTS ux_prepared_wireguard_peers_active_peer_name
  ON prepared_wireguard_peers (tenant_id, peer_name)
  WHERE status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied');

CREATE UNIQUE INDEX IF NOT EXISTS ux_prepared_wireguard_peers_active_public_key
  ON prepared_wireguard_peers (tenant_id, router_public_key)
  WHERE router_public_key <> '' AND status IN ('prepared', 'waiting_router_key', 'ready_to_apply', 'applied');
