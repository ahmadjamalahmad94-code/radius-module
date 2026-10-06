# SEC-360 — أسرار المشترك في حمولات تفاصيل المشترك (API v1)

الفرع: `agent/sec-360-secrets` (من `release/security-rc1`). محلّي فقط؛ لا نشر.

## ما فُحص

| النقطة | النتيجة قبل الإصلاح |
|---|---|
| `GET /api/v1/accounts/<u>/360` | **تسريب**: يُعيد صفّ `subscribers` كاملًا (`SELECT *`)، وقائمة المنع `_SENSITIVE_360_KEYS` فيها `password` فقط — فكان `pppoe_password` يخرج **نصًّا صريحًا لكلّ من يملك صلاحيّة 360**، حتّى المدير الذي لا يملك «رؤية كلمة مرور المشترك». |
| `GET /api/v1/accounts/<u>` و`GET /api/v1/accounts` (القائمة) | سليم: `_serialize` يحذف `password` ويقنّع `pppoe_password` بـ`••••••` ما لم يملك المدير «رؤية كلمة مرور المشترك» (`can_view_subscriber_passwords`) — نفس قاعدة صفحة الويب. |
| `radcheck` / `radpostauth.pass` | لا يُرجَعان: 360 ينتقي أعمدة `radpostauth` بلا `pass`، ولا يقرأ `radcheck`. |
| بوّابة المشترك `/api/v1/portal/...` | سليمة: `_subscriber_row` يحذف `password` و`pppoe_password`. |
| سلّة المحذوفات | سليمة: `_serialize_deleted` قائمة بيضاء. |
| تصدير التقارير (`/reports/*/export.*`) | `_MASK_KEYS` يقنّع `password/pppoe_password/pin/secret`. |

## تغيير السلوك

1. **360 يقنّع `pppoe_password` دائمًا** (بغضّ النظر عن الصلاحيّة): القيمة `"••••••"` إن وُجدت كلمة، و`""` إن لم توجد، مع حقل جديد `subscriber.has_pppoe_password` (bool). المفتاح نفسه باقٍ كي لا تنكسر نسخ التطبيق القديمة التي تحلّله.
2. توسيع قائمة المنع في 360 (دفاع بالعمق) لتشمل: `cleartext_password, card_password, wifi_password, old_password, new_password, api_key, api_token, token, token_hash`.
3. `GET /api/v1/accounts/<u>` بلا تغيير: هو مسار الكشف المحكوم بالصلاحيّة (مماثل للويب). لم نُضِف نقطة «كشف» منفصلة لأنّ التطبيق لا يعرض الكلمة (انظر أدناه).

الملفّات: `app/api/v1/accounts.py` (دالّة `accounts_360` و`_SENSITIVE_360_KEYS`)، الاختبار `tests/test_sec_360_secrets.py`.

## أثر التطبيق (Flutter، `rma-main` فرع `feat/backend-sync-2026-09`)

- `subscribers_repository.dart` يستدعي `/360` و`/accounts/<u>`؛ `subscriber_model.dart` يحلّل `pppoe_password` إلى `pppoePassword` لكنّه **لا يُعرض في أيّ شاشة ولا يُرسَل في PATCH** (بحث `pppoePassword` لا يجد استعمالًا خارج النموذج و`copyWith`).
- إذن **لا أثر وظيفيّ**: سيرى النموذج `••••••` بدل القيمة. ولو أُرسلت القيمة المقنّعة يومًا في PATCH فالخادم يتجاهلها أصلًا (`body.pppoe_password == MASK` تُحذف).
- لا حاجة لنقطة كشف مع سجلّ تدقيق الآن؛ إن أراد التطبيق عرضها مستقبلًا فلتكن نقطة مستقلّة (`POST /accounts/<u>/reveal-pppoe-password`) محكومة بـ«رؤية كلمة مرور المشترك» وتكتب سجلّ نشاط المدير، لا ضمن الحمولات العامّة.

## ملاحظة خارج النطاق

`GET /api/v1/card-users/<id>/360` يُعيد `cards[].password` (كلمات الكروت التي اشتراها مستخدم الكرت). هذا مقصود ظاهريًّا (التطبيق يحلّلها في `card_users_model.dart`) لكنّه غير محكوم بصلاحيّة رؤية كلمات المرور — يستحقّ مراجعة منفصلة.

## الاختبارات

- `tests/test_sec_360_secrets.py`: فشل قبل الإصلاح (تسريب `PppoeSecret-…` في 360)، ونجح بعده؛ ويتحقّق أنّ `/accounts/<u>` والقائمة لا تحويان كلمة الدخول ولا قيمة `radcheck`.
