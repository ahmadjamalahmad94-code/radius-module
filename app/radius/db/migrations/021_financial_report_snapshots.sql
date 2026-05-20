-- Core Stabilization S5:
-- Immutable report snapshot storage for later financial exports. Additive only.

CREATE TABLE financial_report_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    report_type     TEXT NOT NULL,
    date_from       TEXT DEFAULT '',
    date_to         TEXT DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    result_json     TEXT NOT NULL DEFAULT '{}',
    source          TEXT NOT NULL DEFAULT 'ledger',
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX idx_fin_report_snapshots
ON financial_report_snapshots(tenant_id, report_type, created_at);
