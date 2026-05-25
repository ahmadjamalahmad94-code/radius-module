-- Service apply engine for approved collection payments.
-- This slice records intended entitlement application only. It does not call
-- RADIUS, CoA, MikroTik, or any live network side effect.

ALTER TABLE payment_requests
  ADD COLUMN service_apply_status TEXT NOT NULL DEFAULT 'not_applied';

ALTER TABLE payment_requests
  ADD COLUMN service_apply_attempt_id INTEGER;

ALTER TABLE payment_requests
  ADD COLUMN service_applied_at TEXT;

CREATE TABLE IF NOT EXISTS payment_service_apply_attempts (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           INTEGER NOT NULL,
  payment_request_id  INTEGER NOT NULL,
  status              TEXT NOT NULL,
  actor               TEXT NOT NULL DEFAULT '',
  result_json         TEXT NOT NULL DEFAULT '{}',
  error_message       TEXT NOT NULL DEFAULT '',
  created_at          TEXT NOT NULL,
  CHECK(status IN ('applied', 'failed')),
  FOREIGN KEY(payment_request_id) REFERENCES payment_requests(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_service_apply_once
  ON payment_service_apply_attempts (tenant_id, payment_request_id)
  WHERE status = 'applied';

CREATE INDEX IF NOT EXISTS ix_payment_service_apply_request
  ON payment_service_apply_attempts (tenant_id, payment_request_id, id DESC);
