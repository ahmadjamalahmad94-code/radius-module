-- Expiry support for local runtime service entitlements.
-- Used for service-request trials. Expired entitlements remain visible but are
-- returned disabled in the runtime contract.

ALTER TABLE local_service_entitlements
  ADD COLUMN expires_at TEXT;

CREATE INDEX IF NOT EXISTS ix_local_service_entitlements_expiry
  ON local_service_entitlements (tenant_id, expires_at);
