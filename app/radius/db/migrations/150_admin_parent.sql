-- Sub-managers (F3): a manager granted can_create_sub_managers may create
-- sub-managers UNDER him. parent_admin_id links a sub-manager to its parent.
-- Additive; NULL for all existing admins (no parent). Safe default.

ALTER TABLE admins ADD COLUMN parent_admin_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_admins_parent ON admins (parent_admin_id);
