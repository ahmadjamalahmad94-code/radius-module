-- MT67 — الدولة: على الشبكة وعلى طلب التسجيل.
-- رمز ISO-3166 alpha-2 ('' = غير محدَّدة). تُحدّد المنطقة الزمنية
-- الافتراضيّة عند الإنشاء وتُظهِر للمزوّد من أين يأتي طلبه.
ALTER TABLE tenants ADD COLUMN country TEXT NOT NULL DEFAULT '';
ALTER TABLE signup_requests ADD COLUMN country TEXT NOT NULL DEFAULT '';
