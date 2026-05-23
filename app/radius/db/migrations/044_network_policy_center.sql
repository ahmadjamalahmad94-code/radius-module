-- NPC Phase 1 — Network Policy Center data model.
--
-- Three operator-facing sub-services share one product surface
-- but each owns its own policy + entry tables. A shared
-- deployments + script-versions pair lives at the bottom so
-- the lifecycle plumbing (preview → apply → rollback) can be
-- reused without per-service duplication.
--
-- Sub-services:
--   1. Remote MikroTik Access — operator-controlled inbound
--      admin reach to a tenant router, time-boxed.
--   2. Website / App Blocking — destination block list for
--      hotspot / PPPoE clients.
--   3. Hotspot Walled-Garden Allowlist — pre-auth allowlist
--      entries for the captive portal.
--
-- Schema decisions (mirror VX2):
--   * Enums enforced at the REPO layer, never DB CHECK — keeps
--     forward-compat with new states.
--   * No `private_key`, `password`, `secret` columns on any
--     NPC table. The repo's allow-listed update() refuses to
--     write columns outside its whitelist as a second guard.
--   * `service` discriminator on shared tables uses string
--     literals: 'remote_access' / 'web_block' / 'walled_garden'.
--   * Anchored prefix convention for RouterOS comments lives in
--     the renderer (`HOBE_NPC_<service>:<policy_id>:...`); the
--     DB never stores prefixes — it stores policy IDs the
--     renderer derives prefixes from.
--   * (tenant_id, slug) UNIQUE per sub-service so URL routing
--     is stable. Slug fallback to `policy-<sha1>` for Arabic
--     names lives in the repo (mirrors VX2).

-- ─── 1) Remote MikroTik Access policies ──────────────────────

CREATE TABLE IF NOT EXISTS npc_remote_access_policies (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  router_id           INTEGER NOT NULL,
  name                TEXT NOT NULL,
  slug                TEXT NOT NULL,
  -- Service toggles (which admin ports get opened on the input
  -- chain). Booleans stored as 0/1.
  allow_winbox        INTEGER NOT NULL DEFAULT 1,   -- TCP 8291
  allow_ssh           INTEGER NOT NULL DEFAULT 0,   -- TCP 22
  allow_api           INTEGER NOT NULL DEFAULT 0,   -- TCP 8728
  allow_api_ssl       INTEGER NOT NULL DEFAULT 0,   -- TCP 8729
  allow_webfig_http   INTEGER NOT NULL DEFAULT 0,   -- TCP 80
  allow_webfig_https  INTEGER NOT NULL DEFAULT 1,   -- TCP 443
  -- Source-IP allowlist. Stored as a single MikroTik
  -- address-list name (NPC creates and manages it via the
  -- renderer). Empty = "any source" (must be paired with an
  -- explicit expires_at — repo enforces).
  source_address_list TEXT NOT NULL DEFAULT '',
  -- Hard expiry — ISO-8601. The apply-path renderer emits a
  -- /system scheduler entry that removes the rules at this
  -- time. Empty = no expiry (operator must opt in).
  expires_at          TEXT NOT NULL DEFAULT '',
  -- Optional admin justification surfaced in audit log.
  reason              TEXT NOT NULL DEFAULT '',
  enabled             INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_remote_access_router
  ON npc_remote_access_policies (tenant_id, router_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_npc_remote_access_slug
  ON npc_remote_access_policies (tenant_id, slug);

-- ─── 2) Web-block policies + targets ─────────────────────────

CREATE TABLE IF NOT EXISTS npc_web_block_policies (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  router_id           INTEGER NOT NULL,
  name                TEXT NOT NULL,
  slug                TEXT NOT NULL,
  -- Operational scope. 'all_users' is the only supported value
  -- today; future scopes (per-group, per-subscriber) are added
  -- to the repo enum.
  scope               TEXT NOT NULL DEFAULT 'all_users',
  -- Optional time-of-day window. Empty = always-on. Stored as
  -- a free-form schedule identifier (the planner resolves it
  -- against the existing access_schedule infrastructure).
  schedule_id         TEXT NOT NULL DEFAULT '',
  -- When the address-list is empty (e.g. classifier rejected
  -- every entry), should we fail-open (no rule, traffic flows)
  -- or fail-closed (block everything)? Defaults to fail_open
  -- because surprise blackholes are worse than a silent
  -- no-op.
  fail_open           INTEGER NOT NULL DEFAULT 1,
  enabled             INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_web_block_router
  ON npc_web_block_policies (tenant_id, router_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_npc_web_block_slug
  ON npc_web_block_policies (tenant_id, slug);


CREATE TABLE IF NOT EXISTS npc_web_block_targets (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id         INTEGER NOT NULL,
  -- Classifier output. 'category' is a free-string the operator
  -- can use for grouping (e.g. 'tiktok', 'gambling', 'custom').
  category          TEXT NOT NULL DEFAULT 'custom',
  -- target_type ∈ {'domain', 'ip', 'cidr'} — enforced in repo.
  target_type       TEXT NOT NULL,
  value             TEXT NOT NULL,
  normalized_value  TEXT NOT NULL,
  -- status enum mirrors VX2: 'active' | 'disabled' |
  -- 'invalid' | 'manual_review'. Enforced in repo.
  status            TEXT NOT NULL DEFAULT 'active',
  notes             TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_web_block_targets_policy
  ON npc_web_block_targets (policy_id, category, status);

-- Defence-in-depth dedup at the DB layer: re-importing the same
-- list is idempotent because (policy_id, normalized_value) is
-- unique. The repo `add()` uses ON CONFLICT to update in place.
CREATE UNIQUE INDEX IF NOT EXISTS uq_npc_web_block_targets_dedup
  ON npc_web_block_targets (policy_id, normalized_value);

-- ─── 3) Walled-garden policies + entries ─────────────────────

CREATE TABLE IF NOT EXISTS npc_walled_garden_policies (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  router_id           INTEGER NOT NULL,
  -- The MikroTik hotspot profile this allowlist applies to.
  -- Empty = applies globally; a non-empty string scopes via
  -- /ip/hotspot/walled-garden's `profile` field.
  hotspot_profile     TEXT NOT NULL DEFAULT '',
  name                TEXT NOT NULL,
  slug                TEXT NOT NULL,
  enabled             INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_walled_garden_router
  ON npc_walled_garden_policies (tenant_id, router_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_npc_walled_garden_slug
  ON npc_walled_garden_policies (tenant_id, slug);


CREATE TABLE IF NOT EXISTS npc_walled_garden_entries (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id         INTEGER NOT NULL,
  -- entry_type ∈ {'dst_host', 'dst_address', 'dst_address_list'}
  -- — mirrors /ip/hotspot/walled-garden and /walled-garden/ip
  -- fields. 'dst_host' for domains (regex/wildcard), the others
  -- for L3.
  entry_type        TEXT NOT NULL,
  value             TEXT NOT NULL,
  normalized_value  TEXT NOT NULL,
  -- Optional destination port + protocol for fine-grained
  -- allowlists (e.g. allow only 443/tcp to api.payments.test).
  -- Empty = any.
  dst_port          TEXT NOT NULL DEFAULT '',
  protocol          TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'active',
  notes             TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_walled_garden_entries_policy
  ON npc_walled_garden_entries (policy_id, entry_type, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_npc_walled_garden_entries_dedup
  ON npc_walled_garden_entries (policy_id, entry_type, normalized_value);

-- ─── 4) Shared deployments (one row per policy) ──────────────

CREATE TABLE IF NOT EXISTS npc_deployments (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  -- service discriminator — keeps a single deployments table
  -- across the three sub-services so the lifecycle/reporting
  -- code doesn't have to fork three ways.
  service                  TEXT NOT NULL,
  policy_id                INTEGER NOT NULL,
  router_id                INTEGER NOT NULL,
  -- Lifecycle: 'draft' | 'previewed' | 'applied' | 'failed'
  --           | 'disabled'. Enforced in repo.
  status                   TEXT NOT NULL DEFAULT 'draft',
  generated_script_hash    TEXT NOT NULL DEFAULT '',
  last_preview_at          TEXT NOT NULL DEFAULT '',
  last_applied_at          TEXT NOT NULL DEFAULT '',
  last_error               TEXT NOT NULL DEFAULT '',
  last_audit_id            INTEGER,
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_deployments_policy
  ON npc_deployments (service, policy_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_npc_deployments_router
  ON npc_deployments (tenant_id, router_id, id DESC);

-- ─── 5) Shared script versions (append-only) ─────────────────

CREATE TABLE IF NOT EXISTS npc_script_versions (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  service               TEXT NOT NULL,
  policy_id             INTEGER NOT NULL,
  deployment_id         INTEGER,
  script_hash           TEXT NOT NULL,
  script_body           TEXT NOT NULL,
  rollback_script_body  TEXT NOT NULL DEFAULT '',
  command_count         INTEGER NOT NULL DEFAULT 0,
  generated_by_admin_id INTEGER,
  created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_script_versions_policy
  ON npc_script_versions (service, policy_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_npc_script_versions_hash
  ON npc_script_versions (script_hash);
