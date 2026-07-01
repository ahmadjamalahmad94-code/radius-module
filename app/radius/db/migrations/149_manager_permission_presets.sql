-- Permission-template presets (F2): a named bundle of granular grants the owner
-- saves once and applies to any manager in one click. Additive; safe defaults.
-- A preset snapshots the five granular JSON columns of a manager policy.

CREATE TABLE IF NOT EXISTS manager_permission_presets (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id             INTEGER NOT NULL DEFAULT 1,
  name                  TEXT NOT NULL,
  permissions_json      TEXT NOT NULL DEFAULT '{}',
  limits_json           TEXT NOT NULL DEFAULT '{}',
  section_access_json   TEXT NOT NULL DEFAULT '{}',
  action_grants_json    TEXT NOT NULL DEFAULT '{}',
  field_grants_json     TEXT NOT NULL DEFAULT '{}',
  created_by            INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL,
  updated_at            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_manager_preset_name
  ON manager_permission_presets (tenant_id, name);
