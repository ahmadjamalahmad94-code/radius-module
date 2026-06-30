-- 146 — مِلكية الموزّع للمدير (admin_id) — فهرسة + تعبئة آمنة بأثر رجعيّ.
--
-- العمود ``distributors.admin_id`` (FK→admins) موجود أصلًا منذ 019 لكنه لم
-- يُستعمَل: كل الموزّعين القدامى أُنشئوا بـ admin_id = NULL. هذه الهجرة:
--   1) تُضيف فهرسًا لتسريع قَصْر «الموزع/نقطة البيع» على موزّعي مدير بعينه
--      (الاستعلام ``WHERE tenant_id=? AND admin_id=?`` في list_distributors).
--   2) تُعبّئ المالك بأثر رجعيّ — بحذرٍ شديد: فقط حين يُطابِق ``created_by``
--      (اسم المُنشئ المحفوظ) اسمَ دخول مدير واحدٍ بالضبط. لا تخمين، ولا
--      إسناد متعدّد الاحتمالات — الموزّعون بلا تطابُق فريد يَبقون NULL
--      (المالك/السوبر يَراهم، والمدير المحدود لا — وهو السلوك الصحيح).
--
-- additive/idempotent: CREATE INDEX IF NOT EXISTS + UPDATE مشروط على
-- admin_id IS NULL، فإعادة التشغيل (لو حدثت) لا تُغيّر شيئًا.

CREATE INDEX IF NOT EXISTS ix_distributors_owner
    ON distributors (tenant_id, admin_id);

UPDATE distributors
SET admin_id = (
        SELECT a.id FROM admins a
        WHERE a.username = distributors.created_by
          AND a.deleted_at IS NULL
    )
WHERE admin_id IS NULL
  AND created_by <> ''
  AND (
        SELECT COUNT(*) FROM admins a
        WHERE a.username = distributors.created_by
          AND a.deleted_at IS NULL
      ) = 1;
