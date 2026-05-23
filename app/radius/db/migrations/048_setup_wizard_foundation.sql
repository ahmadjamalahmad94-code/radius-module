-- SW1: Setup wizard persistent state foundation.
-- Safe additive migration only (no destructive operations).

CREATE TABLE IF NOT EXISTS setup_wizard_runs (
  id                            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                     INTEGER NOT NULL DEFAULT 1,
  router_id                     INTEGER,
  status                        TEXT NOT NULL DEFAULT 'active',
  current_step                  TEXT NOT NULL DEFAULT 'welcome',
  internet_source_type          TEXT NOT NULL DEFAULT '',
  selected_wan_interface        TEXT NOT NULL DEFAULT '',
  generated_vpn_ip              TEXT NOT NULL DEFAULT '',
  generated_router_vpn_ip       TEXT NOT NULL DEFAULT '',
  generated_radius_secret_ref   TEXT NOT NULL DEFAULT '',
  generated_api_username        TEXT NOT NULL DEFAULT '',
  verification_status_json      TEXT NOT NULL DEFAULT '{}',
  last_error                    TEXT NOT NULL DEFAULT '',
  created_by                    TEXT NOT NULL DEFAULT '',
  created_at                    TEXT NOT NULL,
  updated_at                    TEXT NOT NULL,
  completed_at                  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_runs_tenant
  ON setup_wizard_runs (tenant_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_runs_status
  ON setup_wizard_runs (tenant_id, status, id DESC);


CREATE TABLE IF NOT EXISTS setup_wizard_steps (
  id                            INTEGER PRIMARY KEY AUTOINCREMENT,
  wizard_run_id                 INTEGER NOT NULL,
  tenant_id                     INTEGER NOT NULL DEFAULT 1,
  step_key                      TEXT NOT NULL,
  status                        TEXT NOT NULL DEFAULT 'pending',
  input_json                    TEXT NOT NULL DEFAULT '{}',
  generated_script              TEXT NOT NULL DEFAULT '',
  rollback_script               TEXT NOT NULL DEFAULT '',
  validation_commands_json      TEXT NOT NULL DEFAULT '[]',
  verification_result_json      TEXT NOT NULL DEFAULT '{}',
  created_at                    TEXT NOT NULL,
  updated_at                    TEXT NOT NULL,
  UNIQUE (wizard_run_id, step_key)
);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_steps_run
  ON setup_wizard_steps (wizard_run_id, id ASC);

CREATE INDEX IF NOT EXISTS ix_setup_wizard_steps_status
  ON setup_wizard_steps (tenant_id, status, id DESC);
