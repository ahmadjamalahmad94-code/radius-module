-- (fix wave 2 integration: renumbered 188 -> 183; never deployed under 188.)
-- permmodel (fix wave 2) — إصلاح بيانات المنح التي أفسدها الحفظ/الفتح (D01 + D02/D10).
--
-- D01: حفظ صفحة المدير أو «أساس الصلاحيات» للدور (حتى بلا تغيير) كان يخزّن
--   False صريحًا لكل فعلٍ مُشتقّ من صلاحية RBAC (لا مربّع له في النموذج) — فينطفئ
--   الحذف/التمديد/التجديد/الكوتا/الرصيد/الدفعة/… بصمت. الأفعال المُشتقّة مصدرها
--   صلاحية الدور وحدها الآن ولا تُحفظ من النموذج؛ هنا نزيل ما خُزِّن منها
--   (للمدراء وللأدوار). القيمة True لها بلا أثر أصلًا فتُزال أيضًا.
-- D02: فتح ملفّ المدير كان يُنشئ/يعيد كتابة صفّ سياسته بكل الأعلام False صريحةً
--   فيغلب علَم الدور («عرض كل المشتركين»). التخزين صار «تجاوزات صريحة فقط»؛
--   هنا نزيل أعلام False المخزّنة للمدراء (False = الافتراض أصلًا، فلا تغيّر
--   شيئًا إلّا إعادة وراثة الدور)، ثم نحذف صفوف السياسة الافتراضيّة كليًّا التي
--   أنشأتها القراءات (لا أعلام ولا منح ولا حدود ولا ائتمان).
--   ملاحظة تشغيليّة: من أطفأ عمدًا علَمًا يمنحه الدور لمديرٍ بعينه يعيد حفظه مرّة.
--
-- (متابعة المالك — «حسب الدور/مسموح/ممنوع»): لا نمسّ قيم أفعال الجلسات/الاتصالات/
--   المتجر (session.* و comms.* و store.*_approve و storeuser.*) المخزّنة. كانت لها
--   مربّعاتٌ حقيقيّة قبل هذه الموجة، فقيمتها قرارٌ متعمَّد من المالك (مثل «تأكيد
--   الإيداع نعم / السحب لا» أو «ممنوع» لمديرٍ يمنحه دوره) — تعرضها الصفحة الآن
--   «مسموح/ممنوع» وتحكم فوق مفتاح الدور. ما يُزال هنا: الأفعال المُشتقّة التي لم
--   يكن لها مربّعٌ أصلًا (False فاسدة من D01) + أعلام False المجمّدة (D02).

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.create"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.create"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.create"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.create"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.delete"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.delete"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.delete"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.delete"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.status"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.status"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.status"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.status"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.extend"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.extend"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.extend"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.extend"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.renew"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.renew"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.renew"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.renew"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.quota"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.quota"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.quota"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.quota"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.balance_add"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.balance_add"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.balance_add"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.balance_add"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.payment"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.payment"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.payment"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.payment"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.loan"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.loan"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.loan"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.loan"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."subscriber.send_credentials"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."subscriber.send_credentials"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."subscriber.send_credentials"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."subscriber.send_credentials"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."cards.generate"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."cards.generate"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."cards.generate"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."cards.generate"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."cards.import"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."cards.import"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."cards.import"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."cards.import"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."cards.revoke"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."cards.revoke"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."cards.revoke"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."cards.revoke"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."cards.batch_ops"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."cards.batch_ops"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."cards.batch_ops"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."cards.batch_ops"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."cards.recharge"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."cards.recharge"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."cards.recharge"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."cards.recharge"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."cards.print"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."cards.print"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."cards.print"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."cards.print"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."batch.edit"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."batch.edit"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."batch.edit"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."batch.edit"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."plan.create"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."plan.create"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."plan.create"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."plan.create"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."plan.edit"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."plan.edit"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."plan.edit"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."plan.edit"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."plan.delete"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."plan.delete"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."plan.delete"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."plan.delete"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET action_grants_json = json_remove(action_grants_json, '$._actions."data.export"')
 WHERE entity_type = 'manager' AND json_valid(action_grants_json)
   AND json_type(action_grants_json, '$._actions."data.export"') IS NOT NULL;
UPDATE roles
   SET granular_grants_json = json_remove(granular_grants_json, '$.action_grants._actions."data.export"')
 WHERE json_valid(granular_grants_json)
   AND json_type(granular_grants_json, '$.action_grants._actions."data.export"') IS NOT NULL;

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_create_batch')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_create_batch') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_create_subscriber')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_create_subscriber') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_activate_subscriber')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_activate_subscriber') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_give_free_days')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_give_free_days') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_give_trial_days')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_give_trial_days') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_give_loan')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_give_loan') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_manage_distributors')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_manage_distributors') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_view_all_subscribers')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_view_all_subscribers') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_view_all_card_batches')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_view_all_card_batches') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_import_batches')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_import_batches') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_see_wholesale')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_see_wholesale') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_see_password')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_see_password') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_create_sub_managers')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_create_sub_managers') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_see_balance')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_see_balance') = 'false';

UPDATE manager_distributor_policies
   SET permissions_json = json_remove(permissions_json, '$.can_see_profit')
 WHERE entity_type = 'manager' AND json_valid(permissions_json)
   AND json_type(permissions_json, '$.can_see_profit') = 'false';

DELETE FROM manager_distributor_policies
 WHERE entity_type = 'manager'
   AND COALESCE(permissions_json, '{}') IN ('', '{}')
   AND COALESCE(action_grants_json, '{}') IN ('', '{}', '{"_actions":{}}')
   AND COALESCE(section_access_json, '{}') IN ('', '{}')
   AND COALESCE(field_grants_json, '{}') IN ('', '{}')
   AND COALESCE(credit_limit_minor, 0) = 0
   AND COALESCE(require_approval_above_minor, 0) = 0
   AND COALESCE(profit_share_percent, 0) = 0
   AND (COALESCE(limits_json, '{}') IN ('', '{}') OR (
        json_valid(limits_json)
        AND COALESCE(json_extract(limits_json, '$.max_free_days'), 0) = 0
        AND COALESCE(json_extract(limits_json, '$.max_trial_days'), 0) = 0
        AND COALESCE(json_extract(limits_json, '$.max_subscribers'), 0) = 0
        AND COALESCE(json_extract(limits_json, '$.max_cards_total'), 0) = 0
        AND COALESCE(json_extract(limits_json, '$.max_cards_daily'), 0) = 0
        AND COALESCE(json_extract(limits_json, '$.grants_expire_at'), '') = ''
        AND CAST(COALESCE(json_extract(limits_json, '$.spend_cap_daily'), 0) AS REAL) = 0
        AND CAST(COALESCE(json_extract(limits_json, '$.spend_cap_monthly'), 0) AS REAL) = 0
        AND CAST(COALESCE(json_extract(limits_json, '$.credit_limit'), 0) AS REAL) = 0
        AND COALESCE(json_extract(limits_json, '$.loan_wallet_deducted'), 1) = 1
        AND COALESCE(json_extract(limits_json, '$.rate_daily'), '{}') = '{}'));
