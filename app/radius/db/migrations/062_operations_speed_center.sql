-- Operations Center and Speed Control Center foundations.
-- Additive dry-run/pending policy tables only; no live CoA or MikroTik mutation.

CREATE TABLE IF NOT EXISTS speed_control_policies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    policy_key          TEXT NOT NULL,
    title               TEXT NOT NULL,
    preset              TEXT NOT NULL DEFAULT 'normal',
    multiplier          REAL NOT NULL DEFAULT 1.0,
    target_json         TEXT NOT NULL DEFAULT '{}',
    preview_json        TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending',
    applied_to_radius   INTEGER NOT NULL DEFAULT 0,
    event_id            INTEGER,
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    UNIQUE(tenant_id, policy_key),
    CHECK (status IN ('pending', 'dry_run_ready', 'applied', 'blocked', 'retired')),
    CHECK (applied_to_radius IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_speed_control_policies_status
ON speed_control_policies(tenant_id, status, id DESC);
