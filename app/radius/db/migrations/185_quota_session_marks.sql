-- ════════════════════════════════════════════════════════════════════════
-- 185 — quota_session_marks: خطّ أساسٍ لكلّ جلسة عند بداية اليوم/الشهر المحلّيّ
--        (موجة الإصلاح 3 — F04 H1)
--
-- كانت الكوتة اليوميّة/الشهريّة تعدّ **الجلسات التي بدأت بعد** منتصف الليل /
-- أوّل الشهر فقط. جلسة PPPoE تبقى متّصلة أيّامًا ⇒ كوتة يوميّة بلا حدّ فعليًّا
-- (200 MB بعد منتصف الليل على باقة 10 MB/يوم ⇒ Access-Accept).
--
-- لكلّ جلسة (radacctid) نحفظ:
--   • آخر لقطة معروفة لعدّاداتها (snap_at = acctupdatetime/stoptime، والبايتات)
--     — تُحدَّث من الكنسة الدوريّة (كلّ دقيقة) ومن مسار المصادقة/Interim،
--   • خطّ أساس اليوم المحلّيّ (day_key) والشهر المحلّيّ (month_key): بايتات
--     الجلسة لحظة بداية النافذة = استيفاءٌ خطّيّ بين آخر لقطة قبل الحدّ وأوّل
--     لقطة بعده (بلا لقطة سابقة: من بداية الجلسة بصفر بايت).
-- استهلاك النافذة = الجلسات التي بدأت داخلها + (عدّاد الجلسة العابرة − خطّ أساسها).
-- المصدر: app/radius/services/quota_period.py (_straddle_usage).
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS quota_session_marks (
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    radacctid       INTEGER NOT NULL,
    username        TEXT NOT NULL DEFAULT '',
    snap_at         TEXT,
    snap_in         INTEGER NOT NULL DEFAULT 0,
    snap_out        INTEGER NOT NULL DEFAULT 0,
    day_key         TEXT,
    day_base_in     INTEGER NOT NULL DEFAULT 0,
    day_base_out    INTEGER NOT NULL DEFAULT 0,
    month_key       TEXT,
    month_base_in   INTEGER NOT NULL DEFAULT 0,
    month_base_out  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT,
    PRIMARY KEY (tenant_id, radacctid)
);

CREATE INDEX IF NOT EXISTS idx_quota_session_marks_user
    ON quota_session_marks(tenant_id, username);
CREATE INDEX IF NOT EXISTS idx_quota_session_marks_updated
    ON quota_session_marks(updated_at);
