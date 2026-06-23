-- ════════════════════════════════════════════════════════════════════════
-- حالة الإشعارات الدوريّة للمراقبة (feat/monitoring-periodic-reminder-digest)
-- يُكمّل تنبيه الانتقال لمرّة واحدة (انقطع/عاد) بإضافتين:
--   • تذكير دوريّ ما دام العنصر مفصولًا/غير متاح (كل فترة قابلة للضبط).
--   • تقرير فحص دوريّ موجز لكامل الأسطول (أجهزة + راوترات) كل فترة أطول.
-- هذا الجدول يَحفظ حالة الخَنق (throttle) كي لا يتكرّر الإرسال كل دورة استطلاع:
--   scope = 'reminder:device:<id>' | 'reminder:router:<id>' | 'digest'
--   down_since   = بداية الانقطاع الحالي (للتذكير فقط) — تُحسَب منه المدّة.
--   last_sent_at = آخر إرسال لهذا النطاق — يُقارَن بالفترة المضبوطة.
-- عند تعافي العنصر يُحذَف صفّ التذكير الخاصّ به (إعادة ضبط الحلقة).
-- الإعدادات (تشغيل/فترة لكلٍّ) في tenant_settings (monitoring.reminder.*/.digest.*).
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS monitoring_notify_state (
  tenant_id    INTEGER NOT NULL DEFAULT 1,
  scope        TEXT    NOT NULL,                 -- reminder:device:<id> | reminder:router:<id> | digest
  down_since   TEXT    NOT NULL DEFAULT '',      -- بداية الانقطاع (تذكير)
  last_sent_at TEXT    NOT NULL DEFAULT '',      -- آخر إرسال (throttle)
  updated_at   TEXT    NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, scope)
);
