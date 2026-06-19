-- 129_hotspot_analytics_assets.sql
-- تكملة مصمّم صفحة الدخول: تحليلات الصفحة + أصول مستضافة (فيديو/خط).
--
-- (1) hotspot_analytics_events — أحداث beacon من صفحات الدخول المنشورة
--     (انطباع/اتصال/نقرة) موسومة بالراوتر والقالب ونوع النشاط ومجموعة
--     A/B. تُجمَّع في لوحة التحليلات per-template/per-vertical/per-bucket.
-- (2) hotspot_assets — ملفات يرفعها المشغّل من المصمّم (فيديو سبلاش/
--     إعلان، خط العلامة) مخزّنة BLOB ومحدودة الحجم؛ تُرفع للراوتر عند
--     النشر فتعمل ذاتيًّا بلا أي walled-garden.

CREATE TABLE IF NOT EXISTS hotspot_analytics_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL,
  nas_id        INTEGER NOT NULL DEFAULT 0,
  template_slug TEXT NOT NULL DEFAULT '',
  vertical      TEXT NOT NULL DEFAULT '',
  event         TEXT NOT NULL,             -- impression | connect | click
  ab_bucket     TEXT NOT NULL DEFAULT '',  -- '' | A | B
  created_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_hs_analytics_tenant_nas
  ON hotspot_analytics_events (tenant_id, nas_id);
CREATE INDEX IF NOT EXISTS ix_hs_analytics_created
  ON hotspot_analytics_events (created_at);

CREATE TABLE IF NOT EXISTS hotspot_assets (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL,
  nas_id        INTEGER NOT NULL DEFAULT 0,
  kind          TEXT NOT NULL,             -- video | font
  filename      TEXT NOT NULL,             -- اسم الملف على الراوتر
  content_type  TEXT NOT NULL DEFAULT '',
  size_bytes    INTEGER NOT NULL DEFAULT 0,
  content       BLOB NOT NULL,
  updated_at    TEXT NOT NULL DEFAULT '',
  UNIQUE (tenant_id, nas_id, filename)
);
CREATE INDEX IF NOT EXISTS ix_hs_assets_tenant_nas
  ON hotspot_assets (tenant_id, nas_id);
