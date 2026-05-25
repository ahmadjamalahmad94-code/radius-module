-- Link approved Payment Collection requests to the append-only accounting ledger.
-- The request remains the collection workflow record; ledger entries are the
-- financial source of truth for reports and reconciliation.

ALTER TABLE payment_requests
  ADD COLUMN ledger_entry_id INTEGER;

ALTER TABLE payment_requests
  ADD COLUMN ledger_applied_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_collection_ledger_source
  ON accounting_ledger_entries (tenant_id, source_type, source_id)
  WHERE source_type = 'payment_collection_request';

CREATE INDEX IF NOT EXISTS ix_payment_requests_ledger_entry
  ON payment_requests (tenant_id, ledger_entry_id);
