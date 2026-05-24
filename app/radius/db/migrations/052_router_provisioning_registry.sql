-- Setup Wizard router provisioning registry and IP allocation ledger.
-- Additive only. No plaintext secret material is stored here.

CREATE TABLE IF NOT EXISTS router_provisioning_registry (
  id                           INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                    INTEGER NOT NULL DEFAULT 1,
  wizard_run_id                INTEGER,
  router_label                 TEXT NOT NULL DEFAULT '',
  router_identity              TEXT NOT NULL DEFAULT '',
  status                       TEXT NOT NULL DEFAULT 'reserved',
  vpn_pool_cidr                TEXT NOT NULL,
  router_vpn_ip                TEXT NOT NULL,
  server_vpn_ip                TEXT NOT NULL,
  wireguard_interface_name     TEXT NOT NULL DEFAULT 'hr-wg',
  wireguard_peer_name          TEXT NOT NULL,
  wireguard_public_key         TEXT NOT NULL DEFAULT '',
  wireguard_private_key_ref    TEXT NOT NULL DEFAULT '',
  radius_secret_ref            TEXT NOT NULL DEFAULT '',
  api_username                 TEXT NOT NULL DEFAULT '',
  api_password_ref             TEXT NOT NULL DEFAULT '',
  allocation_index             INTEGER NOT NULL,
  created_at                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  retired_at                   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_router_provisioning_registry_tenant
  ON router_provisioning_registry (tenant_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_router_provisioning_registry_status
  ON router_provisioning_registry (tenant_id, status, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_router_provisioning_registry_active_run
  ON router_provisioning_registry (tenant_id, wizard_run_id)
  WHERE wizard_run_id IS NOT NULL AND status IN ('reserved', 'generated', 'applied', 'verified');

CREATE UNIQUE INDEX IF NOT EXISTS ux_router_provisioning_registry_active_ip
  ON router_provisioning_registry (tenant_id, router_vpn_ip)
  WHERE status IN ('reserved', 'generated', 'applied', 'verified');

CREATE UNIQUE INDEX IF NOT EXISTS ux_router_provisioning_registry_active_index
  ON router_provisioning_registry (tenant_id, allocation_index)
  WHERE status IN ('reserved', 'generated', 'applied', 'verified');


CREATE TABLE IF NOT EXISTS router_ip_allocations (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  registry_id          INTEGER NOT NULL,
  tenant_id            INTEGER NOT NULL DEFAULT 1,
  pool_name            TEXT NOT NULL,
  ip_address           TEXT NOT NULL,
  allocation_type      TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'reserved',
  created_at           TEXT NOT NULL,
  released_at          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_router_ip_allocations_registry
  ON router_ip_allocations (registry_id, id ASC);

CREATE INDEX IF NOT EXISTS ix_router_ip_allocations_pool
  ON router_ip_allocations (tenant_id, pool_name, status, id ASC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_router_ip_allocations_active_ip
  ON router_ip_allocations (tenant_id, pool_name, ip_address, allocation_type)
  WHERE status IN ('reserved', 'active');
