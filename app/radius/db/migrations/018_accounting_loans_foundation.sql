-- R2 foundation: append-only accounting ledger, loans, payments, settlements.
-- Additive only. No existing billing tables are dropped or rewritten.

CREATE TABLE accounting_ledger_entries (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id              INTEGER NOT NULL,
    entry_type             TEXT NOT NULL,
    direction              TEXT NOT NULL DEFAULT 'credit',
    amount                 REAL NOT NULL,
    currency               TEXT NOT NULL DEFAULT 'JOD',
    subscriber_id          INTEGER,
    username               TEXT DEFAULT '',
    admin_id               INTEGER DEFAULT 0,
    operator               TEXT DEFAULT '',
    source_type            TEXT NOT NULL DEFAULT '',
    source_id              INTEGER,
    related_type           TEXT DEFAULT '',
    related_id             INTEGER,
    reversal_of_entry_id   INTEGER,
    status                 TEXT NOT NULL DEFAULT 'posted',
    notes                  TEXT DEFAULT '',
    metadata_json          TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE SET NULL,
    FOREIGN KEY (reversal_of_entry_id) REFERENCES accounting_ledger_entries(id) ON DELETE RESTRICT
);
CREATE INDEX idx_acct_ledger_tenant_date ON accounting_ledger_entries(tenant_id, created_at);
CREATE INDEX idx_acct_ledger_subscriber ON accounting_ledger_entries(tenant_id, subscriber_id);
CREATE INDEX idx_acct_ledger_type ON accounting_ledger_entries(tenant_id, entry_type, status);

CREATE TABLE payment_transactions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id          INTEGER NOT NULL,
    subscriber_id      INTEGER NOT NULL,
    username           TEXT NOT NULL DEFAULT '',
    plan_id            INTEGER,
    amount             REAL NOT NULL,
    currency           TEXT NOT NULL DEFAULT 'JOD',
    method             TEXT NOT NULL DEFAULT 'cash',
    status             TEXT NOT NULL DEFAULT 'posted',
    plan_price         REAL NOT NULL DEFAULT 0,
    custom_price       REAL,
    discount_amount    REAL NOT NULL DEFAULT 0,
    discount_reason    TEXT DEFAULT '',
    effective_price    REAL NOT NULL DEFAULT 0,
    earned_minutes     INTEGER NOT NULL DEFAULT 0,
    rounding_mode      TEXT NOT NULL DEFAULT 'floor',
    ledger_entry_id    INTEGER,
    created_by         TEXT DEFAULT '',
    notes              TEXT DEFAULT '',
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE RESTRICT,
    FOREIGN KEY (plan_id) REFERENCES access_plans(id) ON DELETE SET NULL,
    FOREIGN KEY (ledger_entry_id) REFERENCES accounting_ledger_entries(id) ON DELETE SET NULL
);
CREATE INDEX idx_payments_tenant_date ON payment_transactions(tenant_id, created_at);
CREATE INDEX idx_payments_subscriber ON payment_transactions(tenant_id, subscriber_id);
CREATE INDEX idx_payments_status ON payment_transactions(tenant_id, status);

CREATE TABLE loan_entries (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id              INTEGER NOT NULL,
    subscriber_id          INTEGER NOT NULL,
    username               TEXT NOT NULL DEFAULT '',
    duration_minutes       INTEGER NOT NULL,
    amount                 REAL NOT NULL DEFAULT 0,
    currency               TEXT NOT NULL DEFAULT 'JOD',
    reason                 TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'open',
    approval_status        TEXT NOT NULL DEFAULT 'not_required',
    starts_at              TEXT NOT NULL,
    ends_at                TEXT NOT NULL,
    max_limit_snapshot     INTEGER NOT NULL DEFAULT 0,
    ledger_entry_id        INTEGER,
    created_by             TEXT DEFAULT '',
    settled_at             TEXT,
    settlement_entry_id    INTEGER,
    metadata_json          TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE RESTRICT,
    FOREIGN KEY (ledger_entry_id) REFERENCES accounting_ledger_entries(id) ON DELETE SET NULL
);
CREATE INDEX idx_loans_tenant_date ON loan_entries(tenant_id, created_at);
CREATE INDEX idx_loans_subscriber_status ON loan_entries(tenant_id, subscriber_id, status);

CREATE TABLE settlement_entries (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id          INTEGER NOT NULL,
    subscriber_id      INTEGER,
    username           TEXT NOT NULL DEFAULT '',
    loan_id            INTEGER,
    payment_id         INTEGER,
    amount             REAL NOT NULL,
    currency           TEXT NOT NULL DEFAULT 'JOD',
    method             TEXT NOT NULL DEFAULT 'manual',
    status             TEXT NOT NULL DEFAULT 'posted',
    ledger_entry_id    INTEGER,
    created_by         TEXT DEFAULT '',
    notes              TEXT DEFAULT '',
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE SET NULL,
    FOREIGN KEY (loan_id) REFERENCES loan_entries(id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_id) REFERENCES payment_transactions(id) ON DELETE SET NULL,
    FOREIGN KEY (ledger_entry_id) REFERENCES accounting_ledger_entries(id) ON DELETE SET NULL
);
CREATE INDEX idx_settlements_tenant_date ON settlement_entries(tenant_id, created_at);
CREATE INDEX idx_settlements_loan ON settlement_entries(tenant_id, loan_id);
