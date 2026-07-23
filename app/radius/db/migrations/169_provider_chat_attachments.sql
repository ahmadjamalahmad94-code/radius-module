-- MT43 — مرفقات محادثة المزوّد↔الشبكة.
--
-- عمودان على جدول الرسائل نفسه (لا جدول مستقلّ): كل رسالة تَحمل مرفقًا
-- واحدًا على الأكثر — يكفي لسياق «دعم/فوترة» ويُبسّط التقديم والحذف.
--   attachment_path : مسار نسبيّ تحت مجلّد مرفقات الشبكة (لا مسار مطلق
--                     يَكشف بنية القرص، ولا اسم يَثق به المستخدم).
--   attachment_name : الاسم الأصليّ للعرض فقط — لا يُستخدَم في المسار.
--   attachment_mime : للتقديم بالنوع الصحيح ولاختيار الأيقونة.
--   attachment_size : بايت، للعرض.
--
-- العزل والأمن يَعيشان في طبقة الخدمة/التقديم لا هنا: الملفّ يُخزَّن
-- تحت مجلّدٍ باسم tenant_id، ويُقدَّم عبر مسارٍ محروسٍ owner/member-only
-- يَقرأ المسار من القاعدة لا من الطلب — فلا اجتياز مسار.

ALTER TABLE provider_chat_messages ADD COLUMN attachment_path TEXT NOT NULL DEFAULT '';
ALTER TABLE provider_chat_messages ADD COLUMN attachment_name TEXT NOT NULL DEFAULT '';
ALTER TABLE provider_chat_messages ADD COLUMN attachment_mime TEXT NOT NULL DEFAULT '';
ALTER TABLE provider_chat_messages ADD COLUMN attachment_size INTEGER NOT NULL DEFAULT 0;
