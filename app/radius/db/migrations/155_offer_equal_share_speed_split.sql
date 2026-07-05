-- 155_offer_equal_share_speed_split.sql
-- «تقسيم السرعة على الأجهزة» على مستوى العرض: العرض يحمل علمَي التوزيع
-- (equal_share_download/upload) كقالب، فتَرِثهما كلّ الكروت/المشتركين المولَّدين
-- منه — كلٌّ يقسم سرعة **جلساته هو** على **أجهزته هو** (لا سرعة العرض على كلّ
-- المشتركين). يوازي device_count/device_limit_mode على العرض. الافتراضيّ 0.
ALTER TABLE card_offers ADD COLUMN equal_share_download INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_offers ADD COLUMN equal_share_upload   INTEGER NOT NULL DEFAULT 0;
