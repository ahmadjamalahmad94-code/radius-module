-- Per-manager daily activity counters (A2): supports daily/monthly SPEND caps
-- and per-action DAILY RATE limits. One row per (manager, day, action_key)
-- with a running count + spent amount. Daily/monthly windows come from the
-- `day` (YYYY-MM-DD) — counters "reset" naturally as the date rolls over.

CREATE TABLE IF NOT EXISTS manager_activity_counters (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  admin_id      INTEGER NOT NULL,
  day           TEXT NOT NULL,              -- YYYY-MM-DD (UTC)
  action_key    TEXT NOT NULL DEFAULT '',   -- '' = spend-only rows
  count         INTEGER NOT NULL DEFAULT 0,
  amount_minor  INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_manager_activity
  ON manager_activity_counters (tenant_id, admin_id, day, action_key);

CREATE INDEX IF NOT EXISTS ix_manager_activity_day
  ON manager_activity_counters (tenant_id, admin_id, day);
