# «الحظر والتحكم بالدخول» (feat/access-control-blocking)

تحكّم بمن يُسمح له بالمصادقة في RADIUS. الإنفاذ وقت المصادقة على الخادم
(policy_engine) — ليس في الواجهة فقط. إضافي/خامل: لا يلمس أي مسار حيّ قائم،
والافتراضات لا تحظر أحدًا حتى يُضيف المشغّل حظرًا أو يُفعّل الحظر التلقائي.

## النموذج
**جدول `access_blocks`** (migration 123) — قائمة الحظر الموحّدة، صفّ لكل حظر:
- `block_type` (النطاق): `subscriber` · `group` · `plan` · `card_batch` ·
  `all_subscribers` · `all_hotspot` · `all_cards` · `all_pppoe` · `ip` · `mac`.
- `target`: القيمة (اسم مستخدم/مجموعة/معرّف/IP/MAC)؛ فارغ للنطاقات الشاملة.
- `duration_mode` (3 أنماط): `permanent` (حتى الرفع) · `daily_window`
  (نافذة يومية متكرّرة `window_start`→`window_end`، تدعم العبور بعد منتصف
  الليل مثل 16:00→08:00) · `until` (`expires_at` ثم ينتهي تلقائيًا).
- `source`: `manual` | `auto` (fail2ban). `active` + ختم الرفع (`cleared_at/by`).

**جدول `login_failure_tracker`** (migration 123) — عدّاد محاولات الفشل
(IP+MAC+username+الوقت) لقرار الحظر التلقائي ضمن نافذة زمنية. يُقلَّم محصورًا.

**MAC العشوائي**: يُعاد استخدام مفتاحَي policy_engine القائمين
`security.block_random_mac_subscribers` / `_cards` (يُعرضان/يُحفظان في نفس الصفحة).

**إعدادات الحظر التلقائي** (tenant_settings، تُحفظ من الصفحة):
`security.autoblock_enabled` · `_threshold` (5) · `_window_sec` (300) ·
`_duration_min` (60) · `_target` (`mac` افتراضيًا | `ip` | `both`).

## نقاط الإنفاذ (policy_engine.authorize)
1. `_check_blocks(sub, plan, req, source, now)` — **أول فحص في السلسلة**: يُرفض
   المحظور فورًا (`reason=access_blocked`) قبل فحص كلمة المرور (لا يُسرّب
   صحّتها، ولا يُغذّي عدّاد fail2ban). يبني `AuthContext` ويستدعي
   `access_control.find_active_block` الذي يطابق النطاق + يتحقّق من السريان
   الزمني (`is_block_in_effect`). `service_type` يُؤخذ من الباقة (تصنيف
   hotspot/pppoe صحيح حتى للكروت).
2. عند أي رفض حقيقي (عدا `access_blocked`) → `_register_failed_attempt` يسجّل
   المحاولة ويُنشئ حظرًا تلقائيًا (`until`) عند بلوغ العتبة (مع منع التكرار).
   يشمل ذلك الرفض المبكر `user_not_found` (brute-force بأسماء عشوائية).
3. `find_active_block` لا يكتب في DB (مسار ساخن)؛ انتهاء `until` يُحتسب منطقيًّا.
   كنس الصفوف المنتهية (active=0) كسول في صفحة الإدارة + `deactivate_expired`.
- **fail-open**: أي خطأ في طبقة الحظر يُرجع سماحًا — لا يكسر الـauth أبدًا.

## الواجهة `/admin/radius/access-control`
صفحة مُدارة (تصميم موحّد، flash-stack بدل alert، تأكيد عبر `data-confirm`):
إعدادات الأمان · نموذج إضافة حظر (نطاق ديناميكي + نمط مدّة) · جدول قائمة الحظر
مع زر رفع. محروسة RBAC: العرض `settings.view`، الكتابة `settings.edit` + CSRF.

## دلالات وحدود (مقصودة وموثّقة)
- **IP = عنوان NAS**: وقت مصادقة RADIUS لا يتوفّر إلا عنوان الراوتر (NAS) لا
  جهاز العميل. لذا «حظر IP» (يدوي/تلقائي) يطابق عنوان الـNAS؛ قد يحظر كل من
  خلفه. لذلك الحظر التلقائي يفترض **MAC** (يميّز الجهاز). مُنبّه في الواجهة.
- **التوقيت**: النوافذ اليومية حائطية محلّية تتبع `billing.timezone_offset`؛
  `until` يُحوَّل من المحلّي إلى UTC عند الإنشاء ويُقارن بـUTC (متّسق مع التلقائي).

## ترقيم migration
استُخدم الرقم **123** (التالي على main). فرع `feat/data-connection-oneclick`
استخدم أيضًا 123 لجدول مختلف؛ الـrunner يتتبّع بالاسم الكامل لا البادئة، فلا
تصادم وظيفي. **إن دُمج الفرعان أعِد ترقيم أحدهما (مثلًا هذا إلى 124)**.

## الاختبارات (tests/test_access_control.py — 36، شغّل الملف وحده)
منطق المدّة الثلاثي + العبور + الإزاحة الزمنية · مطابقة كل نطاق · الإنفاذ في
policy_engine (رفض المحظور لكل نطاق/IP/MAC، قبول الطبيعي) · النافذة اليومية
داخل/خارج · الحظر التلقائي عند N + منع التكرار + التعطيل + المسار الكامل عبر
authorize · الانتهاء التلقائي (until) · الرفع يُلغي · تحقّق المدخلات (IP/until) ·
مسارات الصفحة (عرض/إضافة/رفع/حفظ إعدادات).
