-- Core Stabilization S2:
-- Soft-delete metadata for sensitive operational entities. Additive only.

ALTER TABLE subscribers ADD COLUMN deleted_at TEXT;
ALTER TABLE subscribers ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN delete_reason TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_subs_deleted
ON subscribers(tenant_id, deleted_at, status);

ALTER TABLE access_plans ADD COLUMN deleted_at TEXT;
ALTER TABLE access_plans ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '';
ALTER TABLE access_plans ADD COLUMN delete_reason TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_plans_deleted
ON access_plans(tenant_id, deleted_at, enabled);

ALTER TABLE nas_devices ADD COLUMN deleted_at TEXT;
ALTER TABLE nas_devices ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN delete_reason TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_nas_deleted
ON nas_devices(tenant_id, deleted_at, enabled);

ALTER TABLE admins ADD COLUMN deleted_at TEXT;
ALTER TABLE admins ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN delete_reason TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_admins_deleted
ON admins(deleted_at, enabled);

ALTER TABLE roles ADD COLUMN deleted_at TEXT;
ALTER TABLE roles ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN delete_reason TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_roles_deleted
ON roles(tenant_id, deleted_at, is_system);
