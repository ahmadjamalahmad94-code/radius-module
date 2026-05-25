-- Dashboard/report archive analytics.
-- Additive immutable archive table only; no deletion or financial data mutation.

CREATE TABLE IF NOT EXISTS report_archive_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL DEFAULT 1,
    archive_type        TEXT NOT NULL DEFAULT 'yearly',
    period              TEXT NOT NULL,
    report_type         TEXT NOT NULL,
    summary_json        TEXT NOT NULL DEFAULT '{}',
    source_snapshot_id  INTEGER,
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    UNIQUE(tenant_id, archive_type, period, report_type),
    CHECK (archive_type IN ('daily', 'monthly', 'yearly'))
);

CREATE INDEX IF NOT EXISTS idx_report_archive_snapshots_period
ON report_archive_snapshots(tenant_id, archive_type, period DESC, report_type);
