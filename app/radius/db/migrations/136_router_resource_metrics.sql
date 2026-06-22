-- ════════════════════════════════════════════════════════════════════════
-- مقاييس موارد الراوتر (feat/mikrotik-resource-metrics-alerts)
-- جمع دوريّ عبر RouterOS API فوق نفق الإدارة (PULL، صفر عمل على الراوتر):
--   /system/resource → cpu-load، الذاكرة، القرص، uptime، board/version
--   /system/health   → الحرارة (وقد لا تتوفّر على CHR/x86 ⇒ NULL)
--   /interface/print → عدّادات rx/tx لاشتقاق معدّل الحركة (bps)
-- لا علاقة لهذا بـrouter_metric_samples (093) الذي يعتمد دفع الراوتر — هنا
-- نَسحب نحن. عتبات التنبيه تُخزَّن في tenant_settings (لا جدول هنا).
--
-- router_resource_samples: سلسلة زمنية لكل عيّنة (للعرض + اشتقاق المعدّل).
-- router_resource_state:   صفّ واحد لكل راوتر — حالة تجاوز العتبات (hysteresis)
--                          كي لا يتكرّر التنبيه كل دورة (تنبيه عند العبور +
--                          عند العودة فقط) + مؤشّر آخر عيّنة.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS router_resource_samples (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL DEFAULT 1,
  router_id         INTEGER NOT NULL,
  ok                INTEGER NOT NULL DEFAULT 1,    -- نجح السحب؟ (0 = الراوتر غير قابل للوصول)
  cpu_load          INTEGER,                       -- % (NULL لو لم يُقرأ)
  mem_used_pct      REAL,                          -- % مستخدمة
  mem_total_bytes   INTEGER,
  disk_free_pct     REAL,                          -- % حرّة
  disk_total_bytes  INTEGER,
  temperature_c     REAL,                          -- NULL = لا حسّاس (CHR/x86)
  voltage           REAL,                          -- NULL لو غير متوفّر
  traffic_in_bps    INTEGER,                       -- معدّل مُشتقّ (إجمالي الواجهات)
  traffic_out_bps   INTEGER,
  rx_bytes_total    INTEGER,                       -- لاشتقاق المعدّل في الدورة التالية
  tx_bytes_total    INTEGER,
  uptime            TEXT NOT NULL DEFAULT '',
  board_name        TEXT NOT NULL DEFAULT '',
  version           TEXT NOT NULL DEFAULT '',
  recorded_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_router_resource_samples_router
  ON router_resource_samples (tenant_id, router_id, id DESC);

-- صفّ واحد لكل راوتر: حالة التجاوز الحالية لكل مقياس (JSON) + آخر عيّنة.
CREATE TABLE IF NOT EXISTS router_resource_state (
  tenant_id      INTEGER NOT NULL DEFAULT 1,
  router_id      INTEGER NOT NULL,
  breached_json  TEXT NOT NULL DEFAULT '{}',       -- {"cpu":1,"temp":1,...} المتجاوِزة الآن
  last_sample_id INTEGER,
  updated_at     TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, router_id)
);
