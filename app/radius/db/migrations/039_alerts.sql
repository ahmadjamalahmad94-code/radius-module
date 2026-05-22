-- S6.1 — Smart-alert storage.
--
-- One row per *active* condition the alert scanner detected.
-- Deduplication key is (tenant_id, dedup_key) — the scanner
-- builds the dedup_key from rule + router + signal so a
-- repeated detection of the same condition UPDATES the
-- existing row (bumps last_seen) instead of inserting a new
-- one. That keeps the alerts list compact even on a noisy
-- fleet.
--
-- Schema is intentionally narrow:
--   id, tenant_id, router_id, rule, severity, title_ar,
--   explanation_ar, recommended_action_ar, evidence_json,
--   dedup_key, status (open|resolved|silenced),
--   first_seen, last_seen, resolved_at.
--
-- All `_ar` columns are operator-facing — kept verbatim, no
-- string-table dance.

CREATE TABLE IF NOT EXISTS alerts (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  router_id              INTEGER,
  rule                   TEXT NOT NULL,
  severity               TEXT NOT NULL DEFAULT 'info',
  title_ar               TEXT NOT NULL,
  explanation_ar         TEXT NOT NULL DEFAULT '',
  recommended_action_ar  TEXT NOT NULL DEFAULT '',
  evidence_json          TEXT NOT NULL DEFAULT '{}',
  dedup_key              TEXT NOT NULL,
  status                 TEXT NOT NULL DEFAULT 'open',
  first_seen             TEXT NOT NULL,
  last_seen              TEXT NOT NULL,
  resolved_at            TEXT NOT NULL DEFAULT ''
);

-- The dedup index — INSERT ... ON CONFLICT relies on this.
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_dedup
  ON alerts (tenant_id, dedup_key);

CREATE INDEX IF NOT EXISTS ix_alerts_status
  ON alerts (tenant_id, status, last_seen DESC);

CREATE INDEX IF NOT EXISTS ix_alerts_router
  ON alerts (tenant_id, router_id, status);
