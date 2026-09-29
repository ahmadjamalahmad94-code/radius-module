-- «شريك/مالك» محلّيّ + ختم الصلاحيات (permmodel, fix wave 2 — D12/D05).
--
-- is_co_owner: شريكٌ بالشبكة يأخذ كلّ ما يأخذه المالك (تجاوز RBAC + الأفعال
--   المقصورة على المالك __super__). يمنحه/يسحبه المالك أو شريكٌ آخر فقط، ولا
--   يستطيع الشريك خفضَ المالك الأصليّ (أصغر معرّف / مالكو لوحة التراخيص) أو حذفه.
--   انظر app/radius/auth/owner.py.
-- authz_epoch: يزيد عند حفظ دور المدير/تغيير دوره/حذف الدور/حفظ منحه/منح الشراكة
--   أو سحبها. الجلسة تحمل نسخته؛ عند الاختلاف تُعاد قراءة صلاحيات الدور وعلَم
--   المالك من القاعدة في الطلب التالي نفسه (لا تسجيل خروج) — فالمنح والسحب
--   يسريان فورًا. انظر auth/session_helpers.refresh_authz_if_stale.
ALTER TABLE admins ADD COLUMN is_co_owner INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admins ADD COLUMN authz_epoch INTEGER NOT NULL DEFAULT 0;
