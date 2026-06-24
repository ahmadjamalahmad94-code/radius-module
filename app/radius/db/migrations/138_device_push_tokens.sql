-- ════════════════════════════════════════════════════════════════════════
-- رموز دفع الأجهزة (FCM device tokens) — feat/fcm-push-sender
-- يَحفظ رمز Firebase Cloud Messaging لكل جهاز جوّال سجّله تطبيق Flutter
-- عبر POST /api/v1/devices/push-token، كي يَستطيع مُرسِل الدفع الخادمي
-- (app/services/fcm_push.py) إيصال الإشعار نفسه الذي يَكتبه الجرس
-- (panel_notifications) إلى أجهزة المستأجر.
--
-- النطاق: tenant-scoped (الإشعارات نفسها tenant-scoped — لا عمود مستلِم
--   لكل مستخدم في panel_notifications)، مع حفظ admin_id المُسجِّل للمرجع.
-- التفرّد: (tenant_id, token) — upsert يُحدّث آخر ظهور/المنصّة بلا تكرار.
-- التقليم: عند إبلاغ FCM أن الرمز غير مُسجَّل (UNREGISTERED) يُحذَف صفّه.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS device_push_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id    INTEGER NOT NULL DEFAULT 1,
  admin_id     INTEGER NOT NULL DEFAULT 0,   -- المستخدم المُسجِّل (0 = توكن بلا أدمن)
  token        TEXT    NOT NULL,             -- رمز FCM (سرّ الجهاز، ليس سرّ الخادم)
  platform     TEXT    NOT NULL DEFAULT '',  -- android | ios | web
  app_version  TEXT    NOT NULL DEFAULT '',  -- نسخة التطبيق (تشخيصيّ)
  last_seen_at TEXT    NOT NULL DEFAULT '',  -- آخر تسجيل/تحديث للرمز
  created_at   TEXT    NOT NULL DEFAULT '',
  UNIQUE (tenant_id, token)
);

CREATE INDEX IF NOT EXISTS idx_push_tokens_tenant
  ON device_push_tokens (tenant_id);
