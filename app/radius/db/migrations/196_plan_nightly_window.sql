-- ════════════════════════════════════════════════════════════════════════
-- 196 — «غير محدود ليلًا»: نافذةُ الليل على الباقة + دفترُ البايتات المجّانيّة
--
-- كان العلَم nightly_unlimited_enabled يُحفظ ولا يقرؤه شيء (قرار المالك
-- 2026-10-06: وصّله). القراءة المحافِظة: ما يُستهلك داخل نافذة الليل لا يُحتسب
-- من الكوتة (الإجماليّة/اليوميّة/الشهريّة). النافذة «من/إلى» HH:MM بالتوقيت
-- المحلّيّ للّوحة (فلسطين Asia/Gaza، بتوقيتٍ صيفيّ)، وقد تعبر منتصف الليل.
--
-- radacct يحمل لكلّ جلسةٍ عدّاداتها التراكميّة فقط، فنحفظ:
--   • quota_night_marks — آخر قراءةٍ مُحاسَبة لكلّ جلسة (الوقت + البايتات)،
--   • quota_night_free  — البايتات التي وقعت داخل نافذة الليل لكلّ جلسة، مجمَّعةً
--     بساعةٍ UTC (bucket) حتى تُطرح من نافذة اليوم/الشهر/الفترة الصحيحة.
-- الفرق بين قراءتين يُقسَم على الزمن (استيفاءٌ خطّيّ) فيُنسب لليل جزؤه الواقع فيه.
-- المصدر: app/radius/services/quota_night.py.
-- ════════════════════════════════════════════════════════════════════════
ALTER TABLE access_plans ADD COLUMN nightly_from TEXT NOT NULL DEFAULT '';
ALTER TABLE access_plans ADD COLUMN nightly_to TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS quota_night_marks (
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    radacctid   INTEGER NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    last_at     TEXT NOT NULL,
    last_in     INTEGER NOT NULL DEFAULT 0,
    last_out    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT,
    PRIMARY KEY (tenant_id, radacctid)
);

CREATE INDEX IF NOT EXISTS idx_quota_night_marks_updated
    ON quota_night_marks(updated_at);

CREATE TABLE IF NOT EXISTS quota_night_free (
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    radacctid   INTEGER NOT NULL,
    bucket      TEXT NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    free_in     INTEGER NOT NULL DEFAULT 0,
    free_out    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT,
    PRIMARY KEY (tenant_id, radacctid, bucket)
);

CREATE INDEX IF NOT EXISTS idx_quota_night_free_user
    ON quota_night_free(tenant_id, username, bucket);
