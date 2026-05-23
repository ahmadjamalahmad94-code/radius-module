-- NPC safe-execution Phase 4 — change sets + per-router results.
--
-- A change_set is one execution attempt against one policy.
-- It records the full envelope:
--   * who requested it
--   * which policy / preview hash / snapshot
--   * what mode (canary / staged / full)
--   * confirmations stored alongside
--   * per-router script + rollback bytes
--   * per-router status + stdout/stderr + error
--   * final aggregated status
--
-- Same defensive posture as every other NPC table:
--   * tenant_id on every row
--   * NO password / secret / private_key columns
--   * status enums enforced in repo
--   * append-only history; rollback creates a NEW change_set
--     that points at the original via `parent_change_set_id`.

CREATE TABLE IF NOT EXISTS npc_change_sets (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  service                  TEXT NOT NULL,
  policy_id                INTEGER NOT NULL,
  -- action_type: 'apply' | 'rollback'. Rollback rows carry
  -- the original apply's id under `parent_change_set_id`.
  action_type              TEXT NOT NULL,
  parent_change_set_id     INTEGER,
  -- mode at request time: 'canary' | 'staged' | 'full' |
  -- 'rollback' (rollback rows ignore mode).
  execution_mode           TEXT NOT NULL DEFAULT 'full',
  -- Aggregated status (one of):
  --   'planned' | 'running' | 'succeeded' | 'failed' |
  --   'partially_succeeded' | 'rolled_back' |
  --   'rollback_pending' | 'rollback_running' |
  --   'rollback_failed' | 'partially_rolled_back'
  status                   TEXT NOT NULL DEFAULT 'planned',
  preview_hash             TEXT NOT NULL DEFAULT '',
  health_score             INTEGER NOT NULL DEFAULT 0,
  health_grade             TEXT NOT NULL DEFAULT '',
  risk_level               TEXT NOT NULL DEFAULT '',
  snapshot_id              INTEGER,
  -- Comma-separated list of router ids requested. The per-
  -- router rows in `npc_change_set_targets` carry the
  -- authoritative breakdown.
  requested_router_ids     TEXT NOT NULL DEFAULT '',
  -- JSON-encoded list of confirmation codes the operator
  -- ticked (e.g. ["confirm_large_blast_radius", ...]).
  confirmations_json       TEXT NOT NULL DEFAULT '[]',
  -- Whether the change_set is a dry-run rehearsal (Phase 2's
  -- snapshot+preview can land in a dry_run change_set ahead
  -- of any live apply). The brief mentioned this column.
  dry_run                  INTEGER NOT NULL DEFAULT 0,
  created_by               TEXT NOT NULL DEFAULT '',
  created_at               TEXT NOT NULL,
  executed_at              TEXT NOT NULL DEFAULT '',
  finished_at              TEXT NOT NULL DEFAULT '',
  rolled_back_at           TEXT NOT NULL DEFAULT '',
  error_message            TEXT NOT NULL DEFAULT '',
  notes                    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_npc_change_sets_policy
  ON npc_change_sets (tenant_id, service, policy_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_npc_change_sets_parent
  ON npc_change_sets (parent_change_set_id);


CREATE TABLE IF NOT EXISTS npc_change_set_targets (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  change_set_id       INTEGER NOT NULL,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  router_id           INTEGER NOT NULL,
  -- Per-router lifecycle status:
  --   'pending' | 'running' | 'succeeded' | 'failed' |
  --   'skipped' | 'rolled_back'
  status              TEXT NOT NULL DEFAULT 'pending',
  -- Bytes used on THIS router. Recorded so a partial rollout
  -- can be replayed / audited exactly.
  rendered_script         TEXT NOT NULL DEFAULT '',
  rollback_script         TEXT NOT NULL DEFAULT '',
  stdout                  TEXT NOT NULL DEFAULT '',
  stderr                  TEXT NOT NULL DEFAULT '',
  error_message           TEXT NOT NULL DEFAULT '',
  started_at              TEXT NOT NULL DEFAULT '',
  finished_at             TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_npc_change_set_targets_cs
  ON npc_change_set_targets (change_set_id, router_id);

CREATE INDEX IF NOT EXISTS ix_npc_change_set_targets_router
  ON npc_change_set_targets (tenant_id, router_id, id DESC);
