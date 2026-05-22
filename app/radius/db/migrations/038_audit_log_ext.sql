-- S2.1 — Extend audit_log with first-class fields for the
-- operation log UI.
--
-- Existing rows carry their data inside `payload_json`. This
-- migration adds promoted columns that let the operator filter
-- by severity / result_status / router_id without parsing JSON
-- on every query. Backward-compat: old rows keep working
-- because every new column has a default; the service layer
-- accepts both shapes.
--
-- Columns added:
--   severity        — info | warning | critical (free string,
--                     no CHECK so future levels are forward-compat)
--   result_status   — '' | success | failed | partial | cancelled
--   router_id       — nullable; populated when the action targets
--                     a specific NAS, so the per-router history
--                     UI can lookup by indexed column
--   error_message   — short string the operator sees on the row
--   before_json     — pre-state snapshot (e.g. enabled=1)
--   after_json      — post-state snapshot (e.g. enabled=0)
--
-- The redaction contract from S1.1 applies to before_json /
-- after_json the same way it applies to payload_json — the
-- service layer goes through the shared `_redact` helper.

ALTER TABLE audit_log
  ADD COLUMN severity TEXT NOT NULL DEFAULT 'info';
ALTER TABLE audit_log
  ADD COLUMN result_status TEXT NOT NULL DEFAULT '';
ALTER TABLE audit_log
  ADD COLUMN router_id INTEGER;
ALTER TABLE audit_log
  ADD COLUMN error_message TEXT NOT NULL DEFAULT '';
ALTER TABLE audit_log
  ADD COLUMN before_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE audit_log
  ADD COLUMN after_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS ix_audit_router
  ON audit_log (tenant_id, router_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_severity
  ON audit_log (tenant_id, severity, created_at DESC);
