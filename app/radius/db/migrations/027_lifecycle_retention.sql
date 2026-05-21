-- Lifecycle retention policies for safe automatic archiving.
-- This migration is additive only. It never deletes or renames existing data.

ALTER TABLE card_batches ADD COLUMN source_type TEXT NOT NULL DEFAULT 'generated';
ALTER TABLE card_batches ADD COLUMN original_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_batches ADD COLUMN settlement_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_batches ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE card_batches ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE card_batches ADD COLUMN retention_expires_at TEXT;
ALTER TABLE card_batches ADD COLUMN auto_archive_at TEXT;

ALTER TABLE cards ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE cards ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE cards ADD COLUMN retention_expires_at TEXT;
ALTER TABLE cards ADD COLUMN auto_archive_at TEXT;

ALTER TABLE subscribers ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE subscribers ADD COLUMN retention_expires_at TEXT;
ALTER TABLE subscribers ADD COLUMN auto_archive_at TEXT;

ALTER TABLE access_plans ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE access_plans ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE access_plans ADD COLUMN retention_expires_at TEXT;
ALTER TABLE access_plans ADD COLUMN auto_archive_at TEXT;

ALTER TABLE nas_devices ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE nas_devices ADD COLUMN retention_expires_at TEXT;
ALTER TABLE nas_devices ADD COLUMN auto_archive_at TEXT;

ALTER TABLE admins ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE admins ADD COLUMN retention_expires_at TEXT;
ALTER TABLE admins ADD COLUMN auto_archive_at TEXT;

ALTER TABLE roles ADD COLUMN archive_source TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN archive_policy_id INTEGER;
ALTER TABLE roles ADD COLUMN retention_expires_at TEXT;
ALTER TABLE roles ADD COLUMN auto_archive_at TEXT;

UPDATE card_batches
SET original_count = CASE
    WHEN COALESCE(original_count, 0) > 0 THEN original_count
    WHEN COALESCE(count, 0) > 0 THEN count
    WHEN COALESCE(generated, 0) > 0 THEN generated
    ELSE 0
END;

UPDATE card_batches
SET settlement_count = CASE
    WHEN COALESCE(settlement_count, 0) > 0 THEN settlement_count
    WHEN COALESCE(original_count, 0) > 0 THEN original_count
    ELSE 0
END;

CREATE TABLE IF NOT EXISTS lifecycle_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL DEFAULT 1,
    entity_type TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    delay_value INTEGER NOT NULL DEFAULT 0,
    delay_unit TEXT NOT NULL DEFAULT 'days',
    action TEXT NOT NULL DEFAULT 'archive',
    retention_value INTEGER NOT NULL DEFAULT 90,
    retention_unit TEXT NOT NULL DEFAULT 'days',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_policies_lookup
    ON lifecycle_policies (tenant_id, entity_type, enabled);

CREATE TABLE IF NOT EXISTS lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL DEFAULT 1,
    policy_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL DEFAULT 'archive',
    scheduled_for TEXT,
    executed_at TEXT,
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_events_entity
    ON lifecycle_events (tenant_id, entity_type, entity_id, created_at);

CREATE INDEX IF NOT EXISTS idx_lifecycle_events_policy
    ON lifecycle_events (tenant_id, policy_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_cards_lifecycle_due
    ON cards (tenant_id, expire_at, deleted_at);

CREATE INDEX IF NOT EXISTS idx_subscribers_lifecycle_due
    ON subscribers (tenant_id, expire_at, deleted_at);
