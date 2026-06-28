-- 142_manager_credit_caps — per-manager monetary trust system.
--
-- A NEW manager has ZERO trust: both caps disabled, amounts 0 → he can do
-- NOTHING that costs money. The super-admin gradually raises his caps.
--
--   debt_cap_*  — max total outstanding DEBT (دين) the manager may owe the
--                 provider (how negative his effective balance may go).
--   loan_cap_*  — max total outstanding ADVANCES (سلف) the manager may extend
--                 to his subscribers.
--
-- Amounts are stored in minor units (× 100), matching wallets.balance_minor.
-- Additive only; SQLite has no ADD COLUMN IF NOT EXISTS, so this file must be
-- applied exactly once (the runner guarantees that).

ALTER TABLE admins ADD COLUMN debt_cap_enabled  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admins ADD COLUMN debt_cap_minor     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admins ADD COLUMN loan_cap_enabled  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admins ADD COLUMN loan_cap_minor     INTEGER NOT NULL DEFAULT 0;

-- Per-manager credit ledger: every shortfall taken AS DEBT, and every ADVANCE
-- extended to a subscriber, is recorded here. Outstanding debt / advances are
-- SUM(amount_minor) over the matching kind (less any settlement entries).
--   kind: 'debt'    — manager owes the provider this much (over-wallet spend).
--         'advance' — manager has this much lent out to subscribers (سلف).
--         'debt_settle' / 'advance_settle' — negative-direction repayments.
CREATE TABLE IF NOT EXISTS manager_credit_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    manager_id      INTEGER NOT NULL,
    kind            TEXT    NOT NULL,
    amount_minor    INTEGER NOT NULL DEFAULT 0,
    currency        TEXT    NOT NULL DEFAULT 'JOD',
    reference_type  TEXT    NOT NULL DEFAULT '',
    reference_id    INTEGER,
    actor           TEXT    NOT NULL DEFAULT '',
    super_override  INTEGER NOT NULL DEFAULT 0,
    notes           TEXT    NOT NULL DEFAULT '',
    metadata_json   TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_manager_credit_ledger_owner
    ON manager_credit_ledger (tenant_id, manager_id, kind, id DESC);
