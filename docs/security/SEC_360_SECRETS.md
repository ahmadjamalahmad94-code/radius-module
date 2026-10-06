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

## المرحلة 2 — كلمات الكروت في حمولات مستخدمي الكروت (API v1)

### ما وُجد

| النقطة | قبل الإصلاح |
|---|---|
| `GET /api/v1/card-users/<id>/360` | **تسريب**: `cards[].password` (صفّ `cards` خامًا بـ`SELECT *`، أو اعتماد البيع الفوريّ) و`purchases[].cred_password` (صفّ `card_user_purchases` خامًا، ومكرَّر داخل `timeline[].item`) — نصًّا صريحًا لكلّ من يملك `store.view` فقط. |
| `POST /api/v1/card-users/<id>/purchase` | **تسريب**: يُعيد صفّ الشراء كاملًا بما فيه `cred_password` لمن يملك `store.user_purchase`. |
| `POST /api/v1/cards/generate` | **فجوة**: يُعيد `cards[].password` لكلّ من يولّد، بينما «توليد» في الويب يحوّل لقائمة الحزم بلا كلمات، وصفحة كروت الحزمة تقنّعها. |

### القاعدة (مطابقة الويب)

صفحة 360 في الويب (`card_user_360.html`) لا تعرض كلمات الكروت إطلاقًا (الرمز فقط)، وكلّ صفحة ويب تعرضها (كروت الحزمة، ملفّ مشتريات العرض، فاحص الكروت/المستخدم) تمرّ بـ`can_view_card_passwords`: **المالك/الشريك، أو `scope.view_passwords`، أو `cards.print`** (الطباعة تسلّم الكلمات أصلًا). الـAPI يستعمل نسخته `app/api/access_control.can_view_card_passwords()` (نفس القاعدة بهويّة التوكن؛ التوكن غير المربوط بمدير = مسموح كما في باقي الحمولات).

- لا تقييد موزّع هنا: مستخدمو الكروت (المتجر) على مستوى المستأجر في الويب أيضًا، والنقطة محروسة أصلًا بـ`web:card_user_360` (`store.view`).

### تغيير السلوك

1. 360: من لا يملك القاعدة يرى `"••••••"` في كلّ `password`/`cred_password` غير فارغ — في `cards` و`purchases` و`timeline` (تقنيع عميق على الحمولة كلّها، `_mask_card_passwords` في `app/api/v1/card_users.py`). المفاتيح باقية.
2. ردّ الشراء: `purchase.cred_password` مقنَّع بنفس القاعدة (الويب يُظهر اسم المستخدم فقط في رسالة النجاح).
3. ردّ التوليد: يمرّ بـ`_serialize_card_read` (نفس تقنيع `GET /cards/batches/<id>/cards` و`/cards/<id>`).
4. أصحاب الصلاحيّة يرون الكلمات كما كانوا.

### أثر التطبيق (`feat/backend-sync-2026-09`)

- `card_user_360_screen.dart` **يعرض** كلمة كلّ كرت مملوك مع زرّ نسخ (`_CopyValue`)، ويعالج القيمة المقنّعة أصلًا (`isMaskedCardPassword` → «••••» + تلميح «لا تملك صلاحية كشفها»). إذن: المسموح لهم بلا تغيير، وغيرهم يرى الحالة المقنّعة المصمَّمة.
- `repo.purchase(...)` يتجاهل جسم الردّ (`Future<void>`)؛ و`card_generate_dialog.dart` يعدّ الكروت فقط (`r.cards.length`) ولا يعرض كلماتها → لا أثر.

### نقاط فُحصت وهي سليمة

- `GET /cards/batches/<id>/cards` و`GET/PATCH /cards/<id>`: مقنّعة مسبقًا (`_serialize_card_read`).
- استيراد الحزم: `has_password` فقط. فاحص الكروت `GET /cards/check`: `has_password` فقط.
- تصدير الحزم `/cards/batches/export.*`: بيانات الحزمة لا الكروت. `print-templates/layout`: صناديق المواضع فقط.
- `/api/v1/store/*` (توكن المشتري): يُعيد كلمات كروت **المشتري نفسه** مقيّدةً بـ`card_user_id` من التوكن — مقصود.
- سلّة المحذوفات: قائمة بيضاء. `sessions`: لا يقرأ عمود الكلمة.

### فجوة متبقّية (قرار المالك)

`GET /api/v1/cards/recharge/<id>` و`/cards/recharge/<id>/cards` (بطاقات الشحن المسبق للمحفظة) تُعيد `password` صريحًا لمن يملك `cards.recharge` — **وكذلك صفحة الويب** `cards_recharge_batch.html` تعرضها بلا تقنيع. الـAPI مطابق للويب، فلم يُغيَّر؛ إن أراد المالك ربطها بـ`can_view_card_passwords` فيجب تغيير الويب والـAPI معًا.

## الاختبارات

- `tests/test_sec_card_user_360_passwords.py`: مدير بـ`store.view` فقط يرى أقنعة في 360 (`cards`/`purchases`/`timeline`) وفي ردّ الشراء؛ `cards.print` أو `scope.view_passwords` أو المالك يرون الكلمات؛ ردّ التوليد مقنَّع لمن لا يملك `cards.print`. فشلت قبل الإصلاح (تسريب `CardSecret-…` ثمّ `InstantSecret-…`).
- `tests/test_sec_360_secrets.py`: فشل قبل الإصلاح (تسريب `PppoeSecret-…` في 360)، ونجح بعده؛ ويتحقّق أنّ `/accounts/<u>` والقائمة لا تحويان كلمة الدخول ولا قيمة `radcheck`.
