-- (fix wave 2 integration: renumbered 187 -> 182; never deployed under 187.)
-- D11: distributors.admin_id كان يحمل معنيين:
--   • «المدير المالك لهذا الموزّع» (صفحة الموزّعين، نطاق المشتركين، migration 146)
--   • «هذا الحساب الإداريّ هو الموزّع نفسه» (توكن التطبيق، api/access_control)
-- فربطُ موزّعٍ بمدير جعل تطبيقَ المدير يعامله كموزّع (تختفي مشتركوه، 403).
-- الفصل: admin_id يبقى «المدير المالك» (كل القرّاء القائمين والبيانات القائمة
-- منذ 146)، وlogin_admin_id جديد = حساب الدخول الذي *هو* الموزّع. لا تعبئة
-- رجعيّة: القيم القائمة في admin_id معناها «المالك» منذ 146.
ALTER TABLE distributors ADD COLUMN login_admin_id INTEGER;
CREATE INDEX IF NOT EXISTS ix_distributors_login_admin ON distributors(tenant_id, login_admin_id);
