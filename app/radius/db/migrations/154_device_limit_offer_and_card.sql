-- 154 — تمديد سلوك «حدّ الأجهزة» إلى مستوى العرض (باقة) والبطاقة الفرديّة.
--
-- متابعة لـ 153 (فصل كروت/مشتركين). الهرميّة الكاملة لحساب الكرت وقت المصادقة
-- (الأخصّ يَغلب): تجاوز البطاقة الفرديّة → إعداد الحزمة (batch) → إعداد العرض
-- (offer، يُطبَع في الحزمة وقت التوليد) → الافتراض العام للكروت.
-- كلّ الأعمدة اختياريّة بقيمة «وراثة» (mode='' ، count=0) فلا يَتغيّر شيء عند
-- الترقية: العروض/الحزم/الكروت القائمة كلّها ترث كما كانت.
--
-- (1) مستوى العرض (باقة) — قالب يُطبَع في الحزمة المُولَّدة منه.
ALTER TABLE card_offers ADD COLUMN device_limit_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE card_offers ADD COLUMN device_count INTEGER NOT NULL DEFAULT 0;

-- (2) تجاوز البطاقة الفرديّة — نظير card_speed_* (migration 024). يَغلب الحزمة.
--     '' / 0 = وراثة (اتبع الحزمة → العرض → العام).
ALTER TABLE cards ADD COLUMN device_limit_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE cards ADD COLUMN device_count INTEGER NOT NULL DEFAULT 0;
