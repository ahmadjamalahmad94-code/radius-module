-- (fix wave 2 integration: renumbered 189 -> 184; never deployed under 189.)
-- D09/D14 (permmodel): «رؤية كل المشتركين/الحزم» على مستوى الدور صار مفتاحًا
-- واحدًا = مفتاح RBAC (scope.view_all_subscribers / scope.view_all_cards) في مصفوفة
-- الدور — كان المفتاح لا يقرؤه شيء، والعلَم الموازي في «أساس الصلاحيات» هو الفعّال.
-- ننقل العلَم الممنوح على الدور إلى مفتاحه كي لا يفقد أيّ دورٍ ما مُنِح.
UPDATE roles
   SET permissions = json_insert(permissions, '$[#]', 'scope.view_all_subscribers')
 WHERE json_valid(permissions) AND json_valid(granular_grants_json)
   AND json_extract(granular_grants_json, '$.flags.can_view_all_subscribers') = 1
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions)
                   WHERE json_each.value = 'scope.view_all_subscribers');
UPDATE roles
   SET permissions = json_insert(permissions, '$[#]', 'scope.view_all_cards')
 WHERE json_valid(permissions) AND json_valid(granular_grants_json)
   AND json_extract(granular_grants_json, '$.flags.can_view_all_card_batches') = 1
   AND NOT EXISTS (SELECT 1 FROM json_each(roles.permissions)
                   WHERE json_each.value = 'scope.view_all_cards');
