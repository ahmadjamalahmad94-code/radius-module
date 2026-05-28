-- Sprint 2 of the Network Operations family — Backend Ping Monitor
-- + Health Status. See docs/network_operations/NETWORK_OPERATIONS_PLAN.md
-- for the full plan + per-column intent.
--
-- This table is append-only ping history. The cron worker
-- (services/network_device_monitor.py) inserts one row per
-- TCP-probe per device. The dashboard summarises recent rows
-- into health tiers (excellent / good / medium / weak / down /
-- unknown) shown in the sprint-1 list page.
--
-- Retention plan (revisited per row volume):
--   • Keep 7 full days at 1-row-per-minute granularity.
--   • Drop / aggregate older than 7 days to hourly buckets.
-- For now the cron worker just inserts; retention housekeeping
-- ships in a later patch once we see real volume.

CREATE TABLE IF NOT EXISTS network_device_checks (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id       INTEGER NOT NULL,
  -- ISO 8601 UTC (`datetime('now')` in SQLite). The cron worker
  -- writes the same `checked_at` it pushed to the parent row
  -- via `set_last_check()`, so the two stay consistent.
  checked_at      TEXT    NOT NULL,
  -- up / down / unknown. Mirrors network_devices.last_status.
  status          TEXT    NOT NULL,
  -- TCP-connect RTT in milliseconds. NULL on `down`.
  latency_ms      REAL,
  -- One-line reason on failure (timeout / refused / DNS / …).
  -- Capped to 200 chars by the writer; SQLite doesn't enforce.
  error_message   TEXT    NOT NULL DEFAULT '',
  -- backend_ping (sprint 2) | router_netwatch (sprint 6).
  -- Lets a future operator filter «show me only the alerts the
  -- router itself raised» vs «only the HobeRadius probes».
  source          TEXT    NOT NULL DEFAULT 'backend_ping',
  FOREIGN KEY (device_id) REFERENCES network_devices(id) ON DELETE CASCADE
);

-- Hot query: «last N checks for device D» (dashboard sparkline,
-- diagnostics page). Compound (device_id, checked_at DESC) gives
-- the index-only-scan SQLite wants.
CREATE INDEX IF NOT EXISTS idx_network_device_checks_device_time
  ON network_device_checks (device_id, checked_at DESC);

-- Cron worker scan: «every row since X for housekeeping». Less
-- hot, but cheap to maintain.
CREATE INDEX IF NOT EXISTS idx_network_device_checks_time
  ON network_device_checks (checked_at);


-- ─── Per-tenant Telegram settings ──────────────────────────────
-- Sprint 2 also adds Telegram-bot config keyed by tenant_id.
-- Stored here (rather than in the global settings KV) so a
-- multi-tenant deployment can keep each tenant's bot routing
-- independent.
--
-- bot_token + chat_id are the only Telegram knobs we expose.
-- If either is empty, the notifier silently skips — alerts
-- fall through to the in-DB audit trail.

CREATE TABLE IF NOT EXISTS tenant_telegram_settings (
  tenant_id     INTEGER PRIMARY KEY,
  bot_token     TEXT NOT NULL DEFAULT '',
  chat_id       TEXT NOT NULL DEFAULT '',
  enabled       INTEGER NOT NULL DEFAULT 0,
  -- Optional thread-id for «forum supergroups» — newer Telegram
  -- feature, lets the operator route to a specific channel in
  -- a multi-topic group. Empty = main group thread.
  thread_id     TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ─── Alert event log + cooldown tracking ───────────────────────
-- One row per fired alert. Two uses:
--   1. Audit — operator can prove «we DID alert at 22:14».
--   2. Cooldown source-of-truth — the notifier reads the last
--      event of (device_id, event_type) before sending a new
--      one; if the elapsed time is below the cooldown window
--      for that event type, we skip (dedup).
--
-- Event types (from the plan):
--   device_down            ← first transition up → down
--   device_up              ← first transition down → up
--   device_high_latency    ← latency over threshold
--   device_unstable        ← N flips in M minutes (future)
--   device_still_down      ← periodic re-poke while down (future)

CREATE TABLE IF NOT EXISTS network_device_alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL,
  device_id     INTEGER NOT NULL,
  event_type    TEXT NOT NULL,
  fired_at      TEXT NOT NULL DEFAULT (datetime('now')),
  -- Whether the notifier actually sent something downstream
  -- (telegram / sms / webhook). On a dedup-skipped fire we
  -- still write the row with delivery=skipped so the cooldown
  -- check finds it next time.
  delivery      TEXT NOT NULL DEFAULT 'sent',  -- sent | skipped | failed
  message       TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (device_id) REFERENCES network_devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_network_device_alerts_dedup
  ON network_device_alerts (device_id, event_type, fired_at DESC);
