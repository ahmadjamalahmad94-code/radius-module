-- 032_print_export_jobs
-- سجل عمليات تصدير قوالب البطاقات. لا يخزن كلمات مرور البطاقات.

CREATE TABLE IF NOT EXISTS print_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL,
    template_id     INTEGER,
    batch_id        INTEGER,
    export_type     TEXT NOT NULL DEFAULT 'sample_pdf',
    status          TEXT NOT NULL DEFAULT 'created',
    card_count      INTEGER NOT NULL DEFAULT 0,
    file_name       TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES card_print_templates(id) ON DELETE SET NULL,
    FOREIGN KEY (batch_id) REFERENCES card_batches(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_print_jobs_tenant_created
ON print_jobs(tenant_id, created_at);

CREATE INDEX IF NOT EXISTS idx_print_jobs_template
ON print_jobs(tenant_id, template_id, created_at);

CREATE INDEX IF NOT EXISTS idx_print_jobs_batch
ON print_jobs(tenant_id, batch_id, created_at);
