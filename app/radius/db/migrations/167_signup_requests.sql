-- MT36 — طلبات الاشتراك من صفحة هبوط المنصّة (وضع الاستضافة المفتوحة).
--
-- زائرٌ غير مسجَّل يملأ نموذج «اطلب شبكتك» على الجذر، فيهبط الطلب هنا
-- ويُشعَر المالك. لا تُنشأ شبكة تلقائيًّا: المالك يُراجع ويوافق من لوحة
-- الاستضافة، فتُنشأ الشبكة عندها وتُربط بالطلب عبر tenant_id.
--
-- ⚠ هذا الجدول **على مستوى المنصّة لا الشبكة**: لا عمود tenant_id للعزل،
-- وإنّما tenant_id هنا يعني «الشبكة التي وُلدت من هذا الطلب» (0 = لم
-- يُوافَق بعد). لذلك يجب استثناؤه من نسخ الشبكات الاحتياطية تمامًا كما
-- استُثني provider_chat_messages — انظر _EXCLUDE في services/tenant_backup.py.
--
-- الحقول مفتوحة نصًّا بلا تحقّق قاسٍ عمدًا: مُدخَل عموميّ من مجهول، فالقيمة
-- تُنظَّف وتُقصَّر في طبقة الخدمة لا في المخطَّط.

CREATE TABLE IF NOT EXISTS signup_requests (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  network_name  TEXT    NOT NULL DEFAULT '',   -- اسم الشبكة المطلوبة
  slug_wanted   TEXT    NOT NULL DEFAULT '',   -- المعرّف المقترح في الرابط
  contact_name  TEXT    NOT NULL DEFAULT '',
  phone         TEXT    NOT NULL DEFAULT '',
  email         TEXT    NOT NULL DEFAULT '',
  note          TEXT    NOT NULL DEFAULT '',
  status        TEXT    NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
  created_at    TEXT    NOT NULL DEFAULT '',
  handled_at    TEXT    NOT NULL DEFAULT '',
  handled_by    TEXT    NOT NULL DEFAULT '',
  tenant_id     INTEGER NOT NULL DEFAULT 0,   -- الشبكة المُنشأة عند الموافقة (0 = لا شيء)
  source_ip     TEXT    NOT NULL DEFAULT '',
  CHECK (status IN ('pending','approved','rejected'))
);

CREATE INDEX IF NOT EXISTS ix_signup_requests_status
  ON signup_requests (status, id DESC);
