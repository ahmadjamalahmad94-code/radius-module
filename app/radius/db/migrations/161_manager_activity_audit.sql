-- 161 — Manager activity audit: request-level interceptor columns.
--
-- The owner wants EVERY manager movement recorded — page visits, mutating
-- actions, and attempts that FAILED / were BLOCKED / had no effect — so they
-- can see exactly which page a manager entered, what they did, and what they
-- tried to do. Rather than a parallel store, we extend the existing audit_log
-- (see 006_logs.sql + 038_audit_log_ext.sql) with a handful of promoted
-- columns the request interceptor (services/manager_activity_audit.py) fills
-- in. The manager-events report reads them for the page / method / outcome
-- columns and the new filters (manager / page / outcome / date range).
--
-- All columns are additive with safe defaults, so every pre-existing writer
-- (the rich field-diff audit for subscriber/offer edits, the CoA/router logs,
-- the retention worker's own summary row, …) keeps working untouched — those
-- rows simply carry the defaults until an interceptor row overwrites them.
--
-- Columns added:
--   http_method  — GET | POST | PUT | PATCH | DELETE (the verb attempted)
--   endpoint     — Flask endpoint name, e.g. 'radius.users_edit'; drives the
--                  Arabic PAGE label + the page filter without parsing JSON
--   outcome      — '' | visit | success | noop | failed | blocked
--                  ('' = a legacy/rich action row; treated as success in the
--                   report's outcome filter — see reports._manager_events_where)
--   status_code  — the HTTP status the request finished with (200/302/403/…)
--   is_visit     — 1 for a plain GET page view (high-volume; pruned on a
--                  SHORTER retention window than action rows — see
--                  services/log_retention.py). 0 for everything else.

ALTER TABLE audit_log
  ADD COLUMN http_method TEXT NOT NULL DEFAULT '';
ALTER TABLE audit_log
  ADD COLUMN endpoint TEXT NOT NULL DEFAULT '';
ALTER TABLE audit_log
  ADD COLUMN outcome TEXT NOT NULL DEFAULT '';
ALTER TABLE audit_log
  ADD COLUMN status_code INTEGER NOT NULL DEFAULT 0;
ALTER TABLE audit_log
  ADD COLUMN is_visit INTEGER NOT NULL DEFAULT 0;

-- Outcome/visit filtering on the manager-events report + retention's per-visit
-- prune both walk (tenant_id, is_visit, outcome) newest-first.
CREATE INDEX IF NOT EXISTS ix_audit_activity
  ON audit_log (tenant_id, is_visit, outcome, created_at DESC);
-- Page filter groups by endpoint within a tenant.
CREATE INDEX IF NOT EXISTS ix_audit_endpoint
  ON audit_log (tenant_id, endpoint, created_at DESC);
