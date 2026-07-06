-- 156_store_permissions_backfill.sql
-- المتجر الإلكترونيّ (2026-07): مجموعة صلاحيّات مستقلّة store.* بدل
-- الاستعارة من cards.view (عرض) و cards.recharge (كتابة).
--
-- الهدف — كسابقه 099: «لا أحد يفقد وصولًا كان يستخدمه فعلًا بعد النشر».
-- الحارس في blueprint.py صار يفحص المفاتيح الجديدة على مسارات المتجر:
--   • العرض (card_marketplace/card_users_list/card_user_360/…) → store.view
--   • إضافة/إدارة باقة                                        → store.package_add
--   • إنشاء مستخدم                                            → store.user_add
--   • تعديل مستخدم (كلمة المرور)                              → store.user_edit
--   • شحن المحفظة                                             → store.user_recharge
--   • الشراء بالنيابة                                         → store.user_purchase
--   • حذف/استعادة مستخدم                                      → store.user_delete
--
-- خريطة الاشتقاق (من الوضع القديم إلى الجديد):
--   من كان يملك cards.recharge كان يُشغّل المتجر فعليًّا (كلّ الكتابة كانت
--   مربوطة به) → يُمنح كلّ مفاتيح الكتابة الجديدة + store.view.
--   من كان يملك store.review (دعم المتجر) → يُمنح store.view كي يظلّ قادرًا
--   على رؤية صفحات المتجر (مستخدمو البطاقات) التي يدعمها.
--
-- ملاحظة مقصودة (إصلاح التسريب): من كان يملك cards.view **فقط** (يرى
-- البطاقات ولا يُشغّل المتجر) لا يُمنح store.view — فيتوقّف ظهور المتجر
-- له، وهو بالضبط سلوك العزل المطلوب.
--
-- تنفيذيًّا (نفس 099):
--   * roles.permissions عمود TEXT فيه JSON array — دوال JSON1.
--   * json_insert مع '$[#]' يُلحق بالنهاية؛ NOT EXISTS يجعل الترحيل idempotent.
--   * دور super_admin لا يُمسّ (التجاوز يتم بالجلسة is_super_admin أصلًا).

-- ── store.view ← (cards.recharge أو store.review) ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.view')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions)
               WHERE value IN ('cards.recharge', 'store.review'))
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.view');

-- ── مفاتيح الكتابة ← cards.recharge (من كان يُشغّل المتجر) ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.package_add')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.package_add');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.user_add')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.user_add');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.user_edit')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.user_edit');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.user_recharge')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.user_recharge');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.user_purchase')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.user_purchase');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'store.user_delete')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'store.user_delete');
