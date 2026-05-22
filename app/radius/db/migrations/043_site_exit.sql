-- VX2.1 — Selected Sites VPS Exit data model.
--
-- A surgical destination-based routing feature: send specific
-- domains/IPs out through a VPS WireGuard tunnel while every
-- other destination keeps using the original WAN. NOT a full-
-- network VPN.
--
-- Five tables, designed so the public/managed surface is
-- exactly what the operator sees in the UI:
--
--   vps_exit_nodes           — the VPS endpoints (metadata only).
--                              We DO NOT store any private keys
--                              here — WireGuard private keys live
--                              outside the platform DB.
--   site_exit_policies       — one policy per (router, exit_node)
--                              tuple, named, with fail_mode.
--   site_exit_targets        — individual domains/IPs/CIDRs.
--   site_exit_deployments    — current deployment state of a
--                              policy on its router (one row per
--                              policy; reused across applies).
--   site_exit_script_versions — script history for audit + safe
--                              reproduction. Body is allowed to
--                              be large; it carries no secrets.
--
-- Enums (status / target_type / fail_mode) are enforced at the
-- REPO layer to stay forward-compat with new values (mirrors
-- the router_backups.reason pattern).

-- ─── 1) VPS exit nodes ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS vps_exit_nodes (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                 INTEGER NOT NULL DEFAULT 1,
  name                      TEXT NOT NULL,
  public_ip                 TEXT NOT NULL DEFAULT '',
  wireguard_interface_name  TEXT NOT NULL DEFAULT '',
  wireguard_gateway_ip      TEXT NOT NULL DEFAULT '',
  tunnel_cidr               TEXT NOT NULL DEFAULT '',
  enabled                   INTEGER NOT NULL DEFAULT 0,
  last_health_status        TEXT NOT NULL DEFAULT '',
  last_handshake_at         TEXT NOT NULL DEFAULT '',
  created_at                TEXT NOT NULL,
  updated_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_vps_exit_nodes_tenant
  ON vps_exit_nodes (tenant_id, enabled);

-- name must be unique per tenant so the UI can show a stable
-- dropdown and the API can target by name when convenient.
CREATE UNIQUE INDEX IF NOT EXISTS uq_vps_exit_nodes_name
  ON vps_exit_nodes (tenant_id, name);

-- ─── 2) Site-exit policies ───────────────────────────────────

CREATE TABLE IF NOT EXISTS site_exit_policies (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  router_id                INTEGER NOT NULL,
  exit_node_id             INTEGER NOT NULL,
  name                     TEXT NOT NULL,
  slug                     TEXT NOT NULL,
  source_scope             TEXT NOT NULL DEFAULT 'all_users',
  fail_mode                TEXT NOT NULL DEFAULT 'block_when_vps_down',
  include_subdomains       INTEGER NOT NULL DEFAULT 1,
  include_router_output    INTEGER NOT NULL DEFAULT 0,
  enabled                  INTEGER NOT NULL DEFAULT 1,
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_site_exit_policies_router
  ON site_exit_policies (tenant_id, router_id);

-- slug is the URL handle — must be unique per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_exit_policies_slug
  ON site_exit_policies (tenant_id, slug);

-- ─── 3) Site-exit targets ────────────────────────────────────

CREATE TABLE IF NOT EXISTS site_exit_targets (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id           INTEGER NOT NULL,
  group_name          TEXT NOT NULL DEFAULT 'manual_review',
  target_type         TEXT NOT NULL,
  value               TEXT NOT NULL,
  normalized_value    TEXT NOT NULL,
  include_www         INTEGER NOT NULL DEFAULT 1,
  include_subdomains  INTEGER NOT NULL DEFAULT 1,
  status              TEXT NOT NULL DEFAULT 'active',
  notes               TEXT NOT NULL DEFAULT '',
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_site_exit_targets_policy
  ON site_exit_targets (policy_id, group_name, status);

-- Defence-in-depth dedup: even if the repo skips a normalize
-- step the DB refuses duplicate (policy_id, normalized_value).
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_exit_targets_dedup
  ON site_exit_targets (policy_id, normalized_value);

-- ─── 4) Site-exit deployments ────────────────────────────────

CREATE TABLE IF NOT EXISTS site_exit_deployments (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  policy_id                INTEGER NOT NULL,
  router_id                INTEGER NOT NULL,
  status                   TEXT NOT NULL DEFAULT 'draft',
  generated_script_hash    TEXT NOT NULL DEFAULT '',
  last_preview_at          TEXT NOT NULL DEFAULT '',
  last_applied_at          TEXT NOT NULL DEFAULT '',
  last_error               TEXT NOT NULL DEFAULT '',
  last_audit_id            INTEGER,
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_site_exit_deployments_policy
  ON site_exit_deployments (policy_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_site_exit_deployments_router
  ON site_exit_deployments (tenant_id, router_id, id DESC);

-- ─── 5) Site-exit script versions ────────────────────────────

CREATE TABLE IF NOT EXISTS site_exit_script_versions (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id             INTEGER NOT NULL,
  deployment_id         INTEGER,
  script_hash           TEXT NOT NULL,
  script_body           TEXT NOT NULL,
  rollback_script_body  TEXT NOT NULL DEFAULT '',
  command_count         INTEGER NOT NULL DEFAULT 0,
  generated_by_admin_id INTEGER,
  created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_site_exit_script_versions_policy
  ON site_exit_script_versions (policy_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_site_exit_script_versions_hash
  ON site_exit_script_versions (script_hash);
