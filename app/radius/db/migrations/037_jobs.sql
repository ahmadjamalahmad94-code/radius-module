-- S1.1 — Generic background-job table.
--
-- The existing print_export_jobs table (032) is print-specific.
-- Phase S needs a shared job table that can host:
--   - print/PDF exports
--   - router programming applies (Q2/Q3)
--   - router diagnostics scans (P7)
--   - hotspot-page deploys (R3)
--   - backups (S8) — created here so the model is ready
--   - bulk router refreshes (S7)
--
-- Lifecycle states (TEXT column with check constraint kept loose
-- for forward-compat, enforced at the repo layer):
--   queued     — created, no worker has picked it up
--   running    — worker is executing
--   waiting    — paused for confirmation / dependency
--   success    — finished OK
--   failed     — finished with error
--   cancelled  — operator-aborted before completion
--
-- Safety contract — enforced at the repo layer, not the DB:
--   * `payload_json` and `result_json` MUST go through the
--     `_redact()` helper before storage. Secret keys (password,
--     api_password, radius_secret, wg_private_key, token, ...)
--     are replaced with "***". DB cannot enforce that, the repo
--     does.
--
-- Indexes pick the three filters the UI / runner need: by tenant
-- (recent list), by router (per-NAS history), by status (worker
-- pickup loop).

CREATE TABLE IF NOT EXISTS jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  type            TEXT    NOT NULL,
  status          TEXT    NOT NULL DEFAULT 'queued',
  progress        INTEGER NOT NULL DEFAULT 0,
  current_step    TEXT    NOT NULL DEFAULT '',
  owner_admin_id  INTEGER,
  router_id       INTEGER,
  payload_json    TEXT    NOT NULL DEFAULT '{}',
  result_json     TEXT    NOT NULL DEFAULT '{}',
  error_message   TEXT    NOT NULL DEFAULT '',
  created_at      TEXT    NOT NULL,
  started_at      TEXT    NOT NULL DEFAULT '',
  finished_at     TEXT    NOT NULL DEFAULT '',
  updated_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_tenant_created
  ON jobs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_router
  ON jobs (router_id);
CREATE INDEX IF NOT EXISTS ix_jobs_status
  ON jobs (status);
