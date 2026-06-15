# «التحكم بالدخول» — طبقتان (feat/access-control-blocking)

قسم واحد، **مفهومان متمايزان**. الإنفاذ وقت مصادقة RADIUS على الخادم
(policy_engine) — ليس الواجهة فقط. إضافي/خامل؛ الافتراضات لا تمنع أحدًا.

## الطبقة A — «تعليق الوصول» (access suspension)
نطاقي: `subscriber` · `group` · `plan` · `card_batch` · `all_subscribers` ·
`all_hotspot` · `all_cards` · `all_pppoe`. يحكم **متى/هل** يُسمح للمشترك
بالدخول (جدولة، تعليق مؤقت) — **ليس حظرًا أمنيًا**. عند الرفض يحصل المستخدم
على **رسالة عربية مهذّبة** تُحمَل في `Reply-Message`:
- نافذة يومية → «لا يمكن تسجيل الدخول بهذا الوقت».
- حتى تاريخ → «تسجيل الدخول معلّق مؤقتاً حتى إشعار لاحق».
- دائم → «تسجيل الدخول معلّق مؤقتاً — راجع الإدارة».
- + إلحاق سبب المشغّل إن أُدخل (يعرف المستخدم «لماذا»).
رمز الرفض الداخلي: `access_suspended`.

## الطبقة B — «حظر» (security block)
`ip` · `mac`، يدوي + **تلقائي** (fail2ban) عند تكرار الفشل، + منع MAC
العشوائي. رسالة أمنية عامّة «الدخول محظور حاليًا — راجع الإدارة». رمز الرفض:
`access_blocked`.

## النموذج (migration 123، schema-heal)
- **`access_blocks`** — تخزين مشترك للطبقتين، التمييز بعمود **`layer`**
  (`suspension`|`block`، يُشتقّ من `block_type` عبر `access_control.layer_of`).
  أعمدة: `block_type`, `target`, `reason`, `duration_mode`
  (`permanent`/`daily_window`/`until`), `window_start/end`, `expires_at`,
  `source` (`manual`/`auto`), `active` + ختم الرفع.
- **`login_failure_tracker`** — عدّاد fail2ban (نافذة + تقليم محصور).
- إعدادات `security.autoblock_*` (افتراض الهدف `mac`) + `security.block_random_mac_*`.

## نقاط الإنفاذ (policy_engine.authorize)
1. `_check_blocks(sub, plan, req, source, now)` **أول السلسلة**: يطابق النطاق
   + السريان الزمني، ويُرجع رفضًا بالرمز/الرسالة بحسب الطبقة عبر
   `access_control.user_message_for` — `Reply-Message` يحمل الرسالة للمستخدم.
   `service_type` يُؤخذ من الباقة (تصنيف hotspot/pppoe صحيح للكروت).
2. أي رفض حقيقي عدا (`access_suspended`/`access_blocked`) →
   `_register_failed_attempt` (عدّاد + حظر تلقائي `until` عند العتبة، بلا تكرار).
   التعليق المجدول لا يُحتسب فشلًا.
3. `find_active_block` لا يكتب في DB (مسار ساخن)؛ انتهاء `until` منطقي؛ الكنس
   كسول في الصفحة + `deactivate_expired`. **fail-open** على أي خطأ.

## الواجهة `/admin/radius/access-control`
صفحة بقسمين: «تعليق الوصول» (نموذج + جدول) و«الحظر الأمني» (إعدادات + نموذج
IP/MAC + جدول). تصميم موحّد، flash-stack بدل alert، `data-confirm` بدل confirm،
RBAC: عرض `settings.view` / كتابة `settings.edit` + CSRF.

## دلالات وحدود (موثّقة)
- **«حظر IP» = عنوان NAS** (وقت المصادقة لا يتوفّر عنوان العميل)؛ لذا الحظر
  التلقائي يفترض **MAC**. مُنبّه في الواجهة.
- **التوقيت**: النوافذ اليومية حائطية محلّية تتبع `billing.timezone_offset`؛
  `until` يُحوَّل من المحلّي إلى UTC عند الإنشاء ويُقارن بـUTC.
- **migration 123** تتشارك بادئتها مع `feat/data-connection-oneclick` (جدولان
  مختلفان، الـrunner يتتبّع بالاسم الكامل)؛ أعِد ترقيم أحدهما عند الدمج.
- إخفاقات `test_failed_login_attempted_password.py` (4) سابقة على main — ليست انحدارًا.

## الاختبارات (tests/test_access_control.py — 40، شغّل الملف وحده)
الطبقة + اشتقاقها + رسائل المستخدم · منطق المدّة الثلاثي + العبور + الإزاحة
الزمنية · مطابقة كل نطاق · تصفية القائمة بالطبقة · الإنفاذ في policy_engine
(تعليق برسالة Reply-Message، حظر IP/MAC، قبول الطبيعي) · النافذة داخل/خارج ·
الحظر التلقائي + منع التكرار + التعطيل + المسار الكامل · الانتهاء التلقائي ·
الرفع · تحقّق المدخلات · مسارات الصفحة.
