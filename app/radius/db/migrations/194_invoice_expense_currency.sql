-- 194 (zero-w3): invoices and company expenses get a per-row currency.
-- Every other money row (payments, ledger, loans, wallets, card batches)
-- stores the currency it was priced in; invoices and company_expenses did not,
-- so they were displayed in TODAY's billing.currency — a row written in JOD
-- turned into ₪ the day the setting changed (per-row currency rule).
-- Backfill: existing rows get the tenant's billing.currency (what they were
-- shown in until now; ILS when unset — the system default).
-- (192/193 left free for the parallel zero-w1/zero-w2 branches.)
ALTER TABLE invoices ADD COLUMN currency TEXT NOT NULL DEFAULT '';
ALTER TABLE company_expenses ADD COLUMN currency TEXT NOT NULL DEFAULT '';
UPDATE invoices SET currency = UPPER(COALESCE(NULLIF(TRIM((
    SELECT s.value FROM tenant_settings s
    WHERE s.tenant_id = invoices.tenant_id AND s.key = 'billing.currency')), ''), 'ILS'))
WHERE currency = '';
UPDATE company_expenses SET currency = UPPER(COALESCE(NULLIF(TRIM((
    SELECT s.value FROM tenant_settings s
    WHERE s.tenant_id = company_expenses.tenant_id AND s.key = 'billing.currency')), ''), 'ILS'))
WHERE currency = '';
