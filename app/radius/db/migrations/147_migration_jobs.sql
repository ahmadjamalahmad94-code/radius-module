-- معالج ترحيل بيانات العملاء — مخزن حالة المهامّ خادميًّا.
-- يحفظ كل عمليّة رفع/تحليل: الملف الخام يبقى على القرص (file_path) ولا يُعاد
-- للمتصفّح؛ نتيجة التحليل (للعرض) وتقرير التنفيذ تُخزَّن JSON. هذا يفصل خطوات
-- المعالج (تحليل→اختيار→معاينة→تأكيد) دون إعادة رفع الملف، ويُبقي كلمات المرور
-- خادميّةً (لا تجول للمتصفّح). additive فقط.

CREATE TABLE IF NOT EXISTS migration_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    token         TEXT NOT NULL UNIQUE,
    filename      TEXT NOT NULL DEFAULT '',
    fmt           TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'analyzed',   -- analyzed | committed | failed
    file_path     TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    analysis_json TEXT NOT NULL DEFAULT '{}',
    report_json   TEXT NOT NULL DEFAULT '{}',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    committed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_migration_jobs_tenant
    ON migration_jobs(tenant_id, created_at DESC);
