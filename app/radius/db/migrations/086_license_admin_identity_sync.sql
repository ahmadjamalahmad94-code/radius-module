-- License admin identity sync.
-- The license panel is the source of truth for managed admin users.
-- Only password hashes are synced; plaintext passwords are never stored here.

ALTER TABLE admins ADD COLUMN external_identity_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN external_subject TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN external_password_hash_scheme TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN external_password_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admins ADD COLUMN managed_by_license_admin INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admins ADD COLUMN external_updated_at TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_admins_external_identity
  ON admins (external_identity_provider, external_subject);

CREATE INDEX IF NOT EXISTS ix_admins_managed_by_license_admin
  ON admins (managed_by_license_admin, enabled);
