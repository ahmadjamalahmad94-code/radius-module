-- 099_granular_permissions_backfill.sql
-- توسعة كتالوج الصلاحيات (2026-06): مفاتيح تشغيلية دقيقة جديدة
-- (users.* العمليات، online.*، cards.* العمليات، admin_pricing.*،
--  admins.deposit_balance/policy، reports.*، scope.*).
--
-- الهدف هنا: «لا أحد يفقد وصولًا بعد النشر» — كل دور قائم يُمنح
-- تلقائيًا المفاتيح الدقيقة الجديدة المشتقّة ممّا يملكه فعلًا الآن،
-- لأن الحارس في blueprint.py صار يفحص المفاتيح الجديدة على مسارات
-- كانت قبل اليوم محروسة بمفاتيح أعرض (أو غير محروسة إلا بالدخول).
--
-- خريطة الاشتقاق (موثَّقة قيدًا بقيد):
--   users.edit      → users.change_status, users.extend, users.change_plan,
--                     users.quota, users.balance_add, users.payments,
--                     users.loans, users.send_message, users.temp_speed
--                     (كل العمليات التشغيلية كانت متاحة عمليًا لمن يملك
--                      users.edit — السرعة المؤقتة كانت محروسة بها نصًّا)
--   users.view      → users.export, online.view, reports.view
--                     (التصدير وشاشة المتصلين وصفحات التقارير كانت
--                      متاحة لكل من يدخل — أقرب مالك منطقي هو من يرى
--                      بيانات المشتركين)
--   users.delete    → (يبقى كما هو — users_bulk_delete صار محروسًا به)
--   sessions.disconnect → online.disconnect, online.lock_mac, online.lock_ip
--                     (شاشة المتصلين هي واجهة قطع الجلسات الفعلية)
--   cards.view      → cards.verify (فاحص البطاقات كان متاحًا لمن يرى البطاقات)
--   cards.generate  → cards.import, cards.recharge, cards.print
--                     (توليد/استيراد/شحن/طباعة كلها «إنتاج بطاقات»)
--   cards.revoke    → cards.edit_batch, cards.batch_ops, cards.restore
--                     (من يملك الإبطال يملك العمليات الإدارية على الحزم)
--   admins.edit     → admins.deposit_balance, admins.policy
--                     (شحن المحافظ وسياسات المشغّلين إدارة مدراء)
--   settings.edit   → reports.finance
--                     (التقارير المالية كانت بلا حارس؛ نمنحها للأدوار
--                      الإدارية العليا غير-super القائمة فقط)
--   admin_pricing.*  لا تُمنح لأحد تلقائيًا — كانت super_admin فقط،
--                     و super_admin يتجاوز الفحص أصلًا (لا فقدان وصول).
--   scope.*          لا تُمنح تلقائيًا ما عدا scope.view_passwords التي
--                     تُمنح لمن يملك cards.view لأن endpoint كشف كلمة
--                     سر البطاقة كان متاحًا قبل اليوم لكل مسجَّل دخول.
--
-- ملاحظات تنفيذية:
--   * roles.permissions عمود TEXT يحوي JSON array — نستخدم دوال JSON1.
--   * دور super_admin (is_system=1 باسم super_admin) لا يُمسّ: التجاوز
--     يتم في الجلسة (is_super_admin) لا عبر القائمة، لكن من باب النظافة
--     نستثنيه من التحديث.
--   * json_insert مع '$[#]' يضيف لنهاية المصفوفة؛ نتحقق بـ NOT EXISTS
--     عبر json_each قبل كل إضافة كي يكون الترحيل idempotent.

-- ── users.edit → المفاتيح التشغيلية التسعة ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.change_status')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.change_status');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.extend')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.extend');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.change_plan')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.change_plan');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.quota')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.quota');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.balance_add')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.balance_add');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.payments')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.payments');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.loans')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.loans');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.send_message')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.send_message');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.temp_speed')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.temp_speed');

-- ── users.view → التصدير + عرض المتصلين + عرض التقارير ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'users.export')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.view')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.export');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'online.view')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.view')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'online.view');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'reports.view')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'users.view')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'reports.view');

-- ── sessions.disconnect → عمليات شاشة المتصلين ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'online.disconnect')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'sessions.disconnect')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'online.disconnect');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'online.lock_mac')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'sessions.disconnect')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'online.lock_mac');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'online.lock_ip')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'sessions.disconnect')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'online.lock_ip');

-- ── cards.view → فاحص البطاقات + كشف كلمة السر (كانا متاحَين بالدخول فقط) ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.verify')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.view')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.verify');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'scope.view_passwords')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.view')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'scope.view_passwords');

-- ── cards.generate → استيراد + شحن + طباعة ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.import')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.generate')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.import');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.recharge')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.generate')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.recharge');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.print')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.generate')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.print');

-- ── cards.revoke → تعديل الحزم + العمليات الجماعية + الاستعادة ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.edit_batch')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.revoke')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.edit_batch');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.batch_ops')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.revoke')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.batch_ops');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cards.restore')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.revoke')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'cards.restore');

-- ── admins.edit → شحن المحافظ + سياسات المشغّلين ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'admins.deposit_balance')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'admins.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'admins.deposit_balance');

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'admins.policy')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'admins.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'admins.policy');

-- ── settings.edit → التقارير المالية (كانت بلا حارس قبل اليوم) ──
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'reports.finance')
 WHERE name <> 'super_admin'
   AND EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'settings.edit')
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions) WHERE value = 'reports.finance');
