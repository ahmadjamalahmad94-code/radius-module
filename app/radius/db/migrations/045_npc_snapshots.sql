-- NPC Phase H — snapshot foundation.
--
-- Two new tables that let future code store a point-in-time
-- snapshot of router state (firewall filters, address-lists,
-- walled-garden entries, scheduler jobs) without anyone
-- actually contacting a router yet.
--
-- This phase ships the schema + the repo + the service. Snapshot
-- *creation* through MikroTik is intentionally not wired —
-- callers hand in pre-collected payloads (e.g. from a future
-- read-only adapter that's separate from any apply path) and
-- the service persists them with secret rejection in place.
--
-- Tables:
--   network_policy_snapshots          — header (one per
--                                       snapshot of a router).
--   network_policy_snapshot_items     — individual stored items
--                                       (one row per firewall
--                                       filter / address-list /
--                                       walled-garden / scheduler
--                                       entry).
--
-- Same defensive posture as Phase 1:
--   * No `private_key`, `password`, `secret` columns.
--   * Repo allow-listed updates.
--   * Tenant scoping on every query.

CREATE TABLE IF NOT EXISTS network_policy_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  router_id     INTEGER NOT NULL,
  -- Optional link back to the policy that triggered the
  -- snapshot. Snapshots can also be ad-hoc — policy_id stays
  -- nullable.
  policy_id     INTEGER,
  policy_type   TEXT NOT NULL DEFAULT '',
  -- snapshot_type catalogues what got captured:
  --   'firewall_filter' | 'address_list' |
  --   'walled_garden'   | 'scheduler'    |
  --   'composite'       — multiple of the above.
  snapshot_type TEXT NOT NULL,
  -- Lifecycle status — pending | stored | expired | failed.
  status        TEXT NOT NULL DEFAULT 'stored',
  created_by    TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL DEFAULT '',
  notes         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_npc_snapshots_router
  ON network_policy_snapshots (tenant_id, router_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_npc_snapshots_policy
  ON network_policy_snapshots (policy_type, policy_id, id DESC);


CREATE TABLE IF NOT EXISTS network_policy_snapshot_items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id    INTEGER NOT NULL,
  -- item_kind narrows what each row represents:
  --   'firewall_filter_rule' | 'address_list_entry' |
  --   'walled_garden_host'   | 'walled_garden_ip'  |
  --   'scheduler_entry'.
  item_kind      TEXT NOT NULL,
  -- Free-form identifier the MikroTik adapter assigned —
  -- usually `.id` from `/ip/firewall/filter print` etc.
  source_id      TEXT NOT NULL DEFAULT '',
  -- The attributes as a JSON dict the planner/renderer can
  -- consume later. Stored as TEXT to avoid sqlite_json
  -- portability headaches.
  payload_json   TEXT NOT NULL DEFAULT '{}',
  -- Operator-friendly excerpt (e.g. the comment) so the UI
  -- can render a list without parsing every JSON blob.
  display_text   TEXT NOT NULL DEFAULT '',
  position       INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_npc_snapshot_items_snap
  ON network_policy_snapshot_items (snapshot_id, item_kind, position);
