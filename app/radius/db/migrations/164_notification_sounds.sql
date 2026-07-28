-- MT90 — أصوات الإشعارات المخصّصة (صوتٌ مسجَّل بدل النغمة).
--
-- الحاجة: المالك يريد لكلّ نوع إشعار صوتَه — «مشترك جديد»، «تعديل بيانات»،
-- «راوتر غير متصل»، «عاد الراوتر» — بدل نغمةٍ واحدة لا تُميّز الحدث. ومصدر
-- الأصوات مركزيّ: تُرفع مرّةً في اللوحة وتُسحب لكلّ النسخ تلقائيًّا.
--
-- لماذا event_key على panel_notifications؟ العمود `type` خشن
-- (subscription/system) — 27 إشعارًا في الإنتاج تحمل النوع نفسه بينما هي
-- «إضافة مشترك» و«تعديل بيانات» و«تمديد وقت». والعنوان نصٌّ حرّ لا يصلح
-- مفتاحًا. فمفتاحٌ صريح، فارغٌ افتراضيًّا كي لا يَكسر أيّ مُنشئٍ قائم.

ALTER TABLE panel_notifications ADD COLUMN event_key TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_panel_notif_event
    ON panel_notifications(tenant_id, event_key);

-- الأصوات. صفٌّ لكلّ مفتاح: مفتاح حدثٍ دقيق، أو 'type:<نوع>' كارتدادٍ خشن،
-- أو '__global__' للصوت العامّ. الترتيب في القراءة: الأدقّ فالأعمّ فالنغمة.
--
-- الصوت مخزَّنٌ base64 في القاعدة لا ملفًّا على القرص: النسخة الاحتياطيّة
-- تحمله معها، ولا يضيع عند إعادة بناء الحاوية (الكود مخبوزٌ في الصورة).
CREATE TABLE IF NOT EXISTS notification_sounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    sound_key   TEXT NOT NULL DEFAULT '',        -- event_key | type:<t> | __global__
    mime        TEXT NOT NULL DEFAULT 'audio/mpeg',
    filename    TEXT NOT NULL DEFAULT '',
    data_b64    TEXT NOT NULL DEFAULT '',
    -- local  = رفعه العميل بنفسه، يتقدّم دائمًا
    -- central = سُحب من اللوحة المركزيّة، يُستبدَل عند كلّ سحبٍ جديد
    origin      TEXT NOT NULL DEFAULT 'local',
    -- بصمة المحتوى: السحب لا يُعيد الكتابة إن لم يتغيّر شيء (لا ضجيج تدقيق)
    checksum    TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT '',
    CHECK (origin IN ('local','central'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_sound_key
    ON notification_sounds(tenant_id, sound_key);
