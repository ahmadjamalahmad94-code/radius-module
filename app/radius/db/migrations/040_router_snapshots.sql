-- S7 — Router snapshot cache.
--
-- One row per router. Holds the last-known good state plus the
-- last error so the UI can paint fresh / stale / failed without
-- ever calling the router during a page render. The snapshot
-- refresh service updates rows; the UI reads them.
--
-- Snapshot fields are intentionally lightweight — we don't try
-- to mirror everything the router exposes. Just the operational
-- bits the operations center cares about:
--   counters_json     — JSON: hotspot_active, ppp_active, RX, TX
--   resource_json     — JSON: cpu, memory, uptime
--   last_success_at   — last clean refresh
--   last_error        — short error text from the last failed refresh
--   last_attempt_at   — timestamp of the last refresh attempt
--                       (success or failure)
--   source            — "live" | "cached" | "wizard-seed"

CREATE TABLE IF NOT EXISTS router_snapshots (
  router_id        INTEGER PRIMARY KEY,
  tenant_id        INTEGER NOT NULL DEFAULT 1,
  counters_json    TEXT    NOT NULL DEFAULT '{}',
  resource_json    TEXT    NOT NULL DEFAULT '{}',
  last_success_at  TEXT    NOT NULL DEFAULT '',
  last_error       TEXT    NOT NULL DEFAULT '',
  last_attempt_at  TEXT    NOT NULL DEFAULT '',
  source           TEXT    NOT NULL DEFAULT 'live',
  updated_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_router_snapshots_tenant
  ON router_snapshots (tenant_id, updated_at DESC);
