-- ════════════════════════════════════════════════════════════════════════
-- مركز الإشعارات الموحّد (العمود الفقري للإشعارات + التواصل)
-- نموذج إشعار واحد يجمع: إشعارات محلّية (قرب انتهاء الاشتراك/الخدمة) +
-- إشعارات مدفوعة من لوحة التراخيص عبر الجسر (source='bridge'). يظهر في
-- جرس شريط الأعلى (الظرف) + صفحة «مركز الإشعارات». read/unread عبر read_at.
-- إزالة التكرار: فهرس فريد جزئي على dedup_key (للمفاتيح غير الفارغة فقط)
-- كي لا تتكرّر إشعارات قرب الانتهاء/الجسر، مع السماح بإشعارات بلا مفتاح.
-- ملاحظة: اسم `panel_notifications` مقصود — جدول 007 القديم `notifications`
-- ميّت (بلا أي مرجع كودي) وبه admin_id NOT NULL/FK لا يناسب إشعارات على
-- مستوى اللوحة يُنشئها العامل بلا سياق مدير. فلا نُعيد استخدامه.
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS panel_notifications (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   INTEGER NOT NULL DEFAULT 1,
  type        TEXT NOT NULL DEFAULT 'system',   -- license|subscription|service|support|billing|system
  severity    TEXT NOT NULL DEFAULT 'info',     -- info|success|warning|critical
  title       TEXT NOT NULL DEFAULT '',
  body        TEXT NOT NULL DEFAULT '',
  link        TEXT NOT NULL DEFAULT '',         -- رابط عميق نسبي لهدف الإشعار
  dedup_key   TEXT NOT NULL DEFAULT '',         -- مفتاح إزالة التكرار (فارغ = بلا)
  source      TEXT NOT NULL DEFAULT 'local',    -- local|bridge
  source_ref  TEXT NOT NULL DEFAULT '',         -- معرّف العنصر في المصدر (للجسر)
  read_at     TEXT NOT NULL DEFAULT '',         -- ISO وقت القراءة ('' = غير مقروء)
  created_at  TEXT NOT NULL DEFAULT '',
  CHECK (severity IN ('info','success','warning','critical')),
  CHECK (source IN ('local','bridge'))
);

-- فريد فقط للمفاتيح غير الفارغة (إزالة تكرار مفاتيح قرب الانتهاء/الجسر).
CREATE UNIQUE INDEX IF NOT EXISTS ux_panel_notifications_dedup
  ON panel_notifications (tenant_id, dedup_key) WHERE dedup_key <> '';

-- عدّ غير المقروء + قائمة الجرس (الأحدث أولًا) بكفاءة.
CREATE INDEX IF NOT EXISTS ix_panel_notifications_unread
  ON panel_notifications (tenant_id, read_at);
CREATE INDEX IF NOT EXISTS ix_panel_notifications_created
  ON panel_notifications (tenant_id, created_at DESC);

-- ════════════════════════════════════════════════════════════════════════
-- قناة تواصل المشغّل ← لوحة التراخيص (تذاكر/شكاوى صادرة).
-- جدول tickets الموجود مرتبط بمشترك (subscriber_id NOT NULL/FK) فلا يناسب
-- رسائل المشغّل للمزوّد. هذا الجدول الخفيف يَحفظ نسخة محلّية + حالة التسليم
-- عبر الجسر (pending→sent/failed) كي يبقى للمشغّل سجلّ بما أرسله.
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS provider_messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  kind          TEXT NOT NULL DEFAULT 'ticket',   -- ticket|complaint|chat
  subject       TEXT NOT NULL DEFAULT '',
  body          TEXT NOT NULL DEFAULT '',
  category      TEXT NOT NULL DEFAULT 'general',
  priority      TEXT NOT NULL DEFAULT 'normal',
  bridge_status TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|failed
  bridge_ref    TEXT NOT NULL DEFAULT '',
  created_by    TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL DEFAULT '',
  CHECK (bridge_status IN ('pending','sent','failed'))
);

CREATE INDEX IF NOT EXISTS ix_provider_messages_created
  ON provider_messages (tenant_id, created_at DESC);
