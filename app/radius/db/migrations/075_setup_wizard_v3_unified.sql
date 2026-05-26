-- Setup Wizard v3 — unified single-page onboarding state.
--
-- v3 reuses setup_wizard_runs but adds columns for the new state machine
-- (see docs/radius/SETUP_WIZARD_V3_DESIGN.md). v2 rows have v3_state IS NULL
-- and continue to work unchanged.
--
-- A v3 run owns one unified .rsc script the router fetches via /wz/<code>.rsc.
-- After handshake + nas/ops registration succeed, the run holds FKs back
-- into nas_devices and mt_operations_routers so the summary page can deep-link.

ALTER TABLE setup_wizard_runs
  ADD COLUMN v3_state TEXT;

ALTER TABLE setup_wizard_runs
  ADD COLUMN v3_diagnostics_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE setup_wizard_runs
  ADD COLUMN api_mode TEXT;

ALTER TABLE setup_wizard_runs
  ADD COLUMN nas_device_id INTEGER;

ALTER TABLE setup_wizard_runs
  ADD COLUMN ops_room_router_id INTEGER;

ALTER TABLE setup_wizard_runs
  ADD COLUMN unified_script_short_code TEXT;

ALTER TABLE setup_wizard_runs
  ADD COLUMN unified_script_sha256 TEXT;

ALTER TABLE setup_wizard_runs
  ADD COLUMN handshake_first_seen_at TEXT;

ALTER TABLE setup_wizard_runs
  ADD COLUMN handshake_last_seen_at TEXT;

ALTER TABLE setup_wizard_runs
  ADD COLUMN v3_completed_at TEXT;

CREATE INDEX IF NOT EXISTS ix_setup_wizard_runs_v3_state
  ON setup_wizard_runs(v3_state)
  WHERE v3_state IS NOT NULL;


-- One row per generated unified script. Short code is the public path
-- segment in /wz/<code>.rsc. TTL is enforced at fetch time (expires_at).
-- fetched_* captures the first GET to detect "router actually pulled it".
CREATE TABLE IF NOT EXISTS setup_wizard_v3_unified_scripts (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id            INTEGER NOT NULL,
  wizard_run_id        INTEGER NOT NULL,
  short_code           TEXT NOT NULL,
  script_body          TEXT NOT NULL,
  script_sha256        TEXT NOT NULL,
  expires_at           TEXT NOT NULL,
  fetched_at           TEXT,
  fetched_user_agent   TEXT,
  fetched_remote_addr  TEXT,
  fetch_count          INTEGER NOT NULL DEFAULT 0,
  created_at           TEXT NOT NULL,
  FOREIGN KEY (wizard_run_id) REFERENCES setup_wizard_runs(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_unified_scripts_code
  ON setup_wizard_v3_unified_scripts(short_code);

CREATE INDEX IF NOT EXISTS ix_unified_scripts_run
  ON setup_wizard_v3_unified_scripts(tenant_id, wizard_run_id);

CREATE INDEX IF NOT EXISTS ix_unified_scripts_expires
  ON setup_wizard_v3_unified_scripts(expires_at)
  WHERE fetched_at IS NULL;


-- v3 auto-verification probes. One row per (run, check, attempt). The
-- worker writes a new row per attempt so we have a forensic timeline of
-- "did handshake ever flap, when did NAS reload fail, etc.".
CREATE TABLE IF NOT EXISTS setup_wizard_v3_probe_attempts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL,
  wizard_run_id     INTEGER NOT NULL,
  v3_state          TEXT NOT NULL,
  probe_kind        TEXT NOT NULL,           -- 'wg_handshake', 'api_tcp', 'radtest', 'ping_router', etc.
  outcome           TEXT NOT NULL,           -- 'ok' | 'fail' | 'inconclusive'
  diagnostic_code   TEXT,                    -- non-null on fail; FK into WIZARD_DIAGNOSTICS catalog
  raw_evidence_json TEXT NOT NULL DEFAULT '{}',
  duration_ms       INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  FOREIGN KEY (wizard_run_id) REFERENCES setup_wizard_runs(id)
);

CREATE INDEX IF NOT EXISTS ix_probe_attempts_run
  ON setup_wizard_v3_probe_attempts(tenant_id, wizard_run_id, created_at);

CREATE INDEX IF NOT EXISTS ix_probe_attempts_outcome
  ON setup_wizard_v3_probe_attempts(outcome, diagnostic_code)
  WHERE outcome != 'ok';


-- Auto-fix attempts. Separate from probes because a fix may run multiple
-- subcommands and needs idempotency tracking.
CREATE TABLE IF NOT EXISTS setup_wizard_v3_auto_fix_attempts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL,
  wizard_run_id     INTEGER NOT NULL,
  diagnostic_code   TEXT NOT NULL,
  status            TEXT NOT NULL,           -- 'applied' | 'failed' | 'skipped_already_fixed'
  actor             TEXT NOT NULL DEFAULT '',
  command_preview   TEXT NOT NULL DEFAULT '',
  result_json       TEXT NOT NULL DEFAULT '{}',
  error_json        TEXT NOT NULL DEFAULT '{}',
  created_at        TEXT NOT NULL,
  applied_at        TEXT,
  FOREIGN KEY (wizard_run_id) REFERENCES setup_wizard_runs(id)
);

CREATE INDEX IF NOT EXISTS ix_auto_fix_run
  ON setup_wizard_v3_auto_fix_attempts(tenant_id, wizard_run_id, created_at);

-- Idempotency: at most one applied auto-fix per (run, diagnostic). Re-run
-- creates a new row only if previous was failed.
CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_fix_applied_once
  ON setup_wizard_v3_auto_fix_attempts(tenant_id, wizard_run_id, diagnostic_code)
  WHERE status = 'applied';
