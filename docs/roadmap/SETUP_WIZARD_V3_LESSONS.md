# سجلّ مشاكل وحلول — Setup Wizard v3

> توثيق شامل لكل مشكلة واجهناها في تقوية معالج إعداد الراوتر v3
> (HobeRadius + FreeRADIUS + MikroTik RouterOS 7.20.6) من أجل
> النشر التجاري، مع الحلّ النهائي لكل واحدة. مرتّب بحسب الفئة لا
> بحسب الزمن.

---

## 1) معماريّة وواجهة (Architecture & UX)

### 1.1 توحيد ثلاث صفحات إدارة الراوتر في hub واحد
- **المشكلة:** كان عندنا `/mt/operations` و `/setup-wizard/fleet` و `/setup-wizard-v3/routers/<id>/services` — ثلاث صفحات منفصلة تعرض نفس المحتوى بصياغات مختلفة.
- **الحلّ:** كل المسارات تحوّلت لـ `302 Redirect` نحو `/mt/<id>/dashboard`، وصارت الـ Dashboard هي الواجهة الوحيدة لجميع عمليات الراوتر.
- **الملفات:**
  - `app/radius/routes/setup_wizard_v3.py` — endpoint redirect
  - `app/templates/radius/mt_dashboard.html` — لوحة موحّدة
  - حُذفت ملفات orphans: `fleet.html`, `services_dashboard.html`

### 1.2 Hub قابل للطيّ + شريط تبويبات
- **المشكلة:** الـ Hub العلوي (معلومات الراوتر + روابط) كان «ماكل صفحة كاملة».
- **الحلّ:** Hub قابل للطيّ يشبه السايد-بار + شريط تبويبات منظّم (Services / خدماتي / Logs ...).
- **CSS:** `details/summary` للطيّ + تحريك سلس عبر `max-height` transition.

### 1.3 رقائق الخدمات (Service Chips)
- **المشكلة:** الخدمات كانت كروت كبيرة دفشة تأخذ مساحة.
- **التطوّر:**
  - النسخة الأولى: كروت كبيرة بحالة + وصف طويل.
  - مرحلة وسطى: كروت أنحف ولكن لسا كثيرة العرض.
  - **النهائي:** Chips في سطر واحد، حجم صغير، badge ملوّن للحالة، أنميشن hover لطيف.
- **القرار:** اعتمدنا تصميم A (chips أفقية) وحذفنا CSS الخاصّ بـ B (الكروت الكبيرة).

### 1.4 إخفاء المحتوى المتقدّم على المسار القديم
- **المشكلة:** المسار القديم `/setup-wizard-v3/...` بعد التوحيد لازم يخفي المحتوى الاحترافي تلقائياً.
- **الحلّ:** Flag في الـ context يخفي القوائم التقنية إن الطلب جا من المسار القديم.

---

## 2) شكل تدفّق الخدمة (Per-service 4-phase shell)

### 2.1 المراحل الأربعة
- **التهيئة (configure)** — استمارة الإدخال.
- **المعاينة (preview)** — bullets قابلة للقراءة + سكربت قابل للنسخ.
- **الإرسال (apply)** — 3 خطوات: اتصال / إرسال / حفظ.
- **التحقّق (verify)** — فحص حقيقي على الراوتر.

### 2.2 «إيقاف الخدمة» في كل تدفّق
- **المشكلة:** ما في طريقة لإلغاء خدمة بعد تفعيلها من نفس مكان تفعيلها.
- **الحلّ:** زر «إيقاف الخدمة» في كل partial، يستدعي endpoint cleanup مخصّص.

### 2.3 شارات الحالة في Service Chips
- **المشكلة:** بدون شارات، الـ chips كلّها تبدو متطابقة بدون أي إشارة لأيّها مفعّل فعلاً.
- **الحلّ:** Badge ملوّن (`is-active` / `is-inactive` / `is-unknown`) يتحدّث من `/inventory` لما الصفحة تُفتح.

---

## 3) أخطاء CSRF واستيرادات

### 3.1 CSRF 400 على كل طلب
- **المشكلة:** كل طلبات POST من الواجهة تطلع `400 Bad Request: CSRF token missing`.
- **السبب:** الاسم المتوقّع هو `_csrf_token` (مع شرطة سفليّة)، الواجهة كانت تستعمل `csrf_token`.
- **الحلّ:** `input[name="csrf_token"]` → `input[name="_csrf_token"]` في 8 ملفات.

### 3.2 استيراد MikrotikClient
- **المشكلة:** `ImportError: cannot import name 'MikrotikClient' from 'services.mikrotik_admin_client'`.
- **السبب:** المسار الصحيح هو `app.radius.integration.mikrotik`، الكود في 7 أماكن كان يستورده من المسار القديم.
- **الحلّ:** توحيد كل الاستيرادات على `from ..integration.mikrotik import MikrotikClient`.

---

## 4) خصوصيّات RouterOS 7.20.6

### 4.1 `/ip/hotspot/server/print` غير موجود
- **المشكلة:** Verify يفشل لأنّ المسار `print` على `server` ما هو موجود.
- **الحلّ:** التغيير إلى `/ip/hotspot/print` (الـ server entries نفسها).

### 4.2 HotspotPhasePlanner يحتاج معطيات
- **المشكلة:** `KeyError: radius_secret` و `KeyError: router_vpn_ip`.
- **الحلّ:** قراءة القيم من `nas_devices` وحقنها قبل استدعاء الـ planner داخل `_plan_hotspot()`.

### 4.3 Broadband `local_address="10.30.0.1/32"` يُرفض
- **المشكلة:** الـ planner كان يمرّر CIDR، لكنّ `IPv4Address` ما يقبل البادئة.
- **الحلّ:** القيمة الافتراضيّة صارت `"10.30.0.1"` (بدون `/32`).

### 4.4 `dns-server="1.1.1.1,8.8.8.8"` يسبّب trap
- **المشكلة:** RouterOS يرفض القيم المفصولة بفواصل في خاصيّات IP-typed.
- **الحلّ النهائي:** الـ post-processor للـ Broadband يحذف `dns-server=` بالكامل (يعتمد على DNS النظام).

### 4.5 `use-radius=yes` على `/ppp profile` غير صالح في RouterOS 7
- **المشكلة:** الـ planner كان يحقنها على البروفايل، RouterOS يرفض.
- **الحلّ:** الـ post-processor للـ Broadband يحذفها من البروفايل ويُضيفها كأمر منفصل: `/ppp aaa set use-radius=yes`.

### 4.6 `/ppp profile set "name"` يرجع «no such item»
- **المشكلة:** RouterOS لا يقبل تمرير الاسم كقيمة مباشرة في `set`.
- **الحلّ:** إعادة كتابة كل `set "name"` إلى `set [find name="name"]`.

### 4.7 ⚠️ `comment` غير متوفّر على `/ip hotspot*` (الاكتشاف الكبير)
- **المشكلة:** حاولنا حقن `comment="HOBERADIUS_SETUP:<id>:hotspot"` على hotspot rows لاستخدامها في التصنيف داخل تبويب «خدماتي».
- **النتائج التتابعيّة:**
  1. حقنها على نفس سطر `/ip hotspot profile add` → **trap عمود 206** (السطر صار >240 حرف + `login-by=...,...` confused parser).
  2. فصلها لسطر `set` منفصل على البروفايل → **trap عمود 59** — `/ip hotspot profile` ما عنده خاصيّة `comment` في 7.20.6!
  3. حصرها على السيرفر (`/ip hotspot set [find] comment=`) → **trap عمود 52** — حتى السيرفر ما عنده `comment`!
- **الحلّ النهائي:** الـ post-processor صار **لا يحقن أي comment** على hotspot. التصنيف يعتمد كلّياً على نمط الاسم (`hotspot-<iface>` / `hsprof-<iface>`) في `_classify_hotspot_source`.

### 4.8 ⚠️ Broadband Replacement Bug — الـ tag مشترك بين كل المداخل
- **المشكلة:** الـ Broadband planner يُصدر كتلة تنظيف بـ tag مشترك `HOBERADIUS_SETUP:<run_id>:broadband`، والـ `run_id` يساوي `router_id` (في `_plan_broadband` السطر 691). يعني كل تشغيل على نفس الراوتر يستعمل نفس الـ tag.
- **النتيجة:** السطر:
  ```
  /interface pppoe-server server remove [find where comment~"<tag>"]
  ```
  يمسح **كل** الـ pppoe-server entries على الراوتر، بما فيهم المداخل الأخرى من تشغيلات سابقة. برمجة ether3 تمسح ether2 بصمت.
- **المقارنة مع Hotspot:** الـ Hotspot يستعمل tag per-interface (`HOBE_HOTSPOT_<iface>`)، فالتنظيف يقتصر على المدخل الواحد. هذا الفرق هو سبب عدم وجود المشكلة في Hotspot.
- **الحل في `_broadband_post_process_script` (step 6):**
  - فحص السكربت لاستخراج `/interface pppoe-server server add interface="X"` → معرفة المداخل المستهدفة في هذا التشغيل.
  - استبدال `[find where comment~"<tag>"]` المشتركة بـ `[find where interface="X" and comment~"<tag>"]` per-interface.
  - حذف `profile remove` / `pool remove` / `nat remove` كلّياً — الـ `:if` guards الموجودة في الـ planner تتعامل مع التكرار، والـ profile/pool/NAT مشتركة بين كل المداخل ولا يجب لمسها (وإلّا قطع الجلسات على المداخل الأخرى).
- **النتيجة:** الـ Broadband صار additive — إضافة مدخل جديد لا تمسح الموجود.

### 4.9 ⚠️ فرق Parser بين Terminal و `/system/script` (الاكتشاف الذهبي)
- **المشكلة:** بعد إصلاح كل ما سبق، الـ trap لسا قائم. المُشغّل لصق السكربت في Terminal يدوياً ونجح!
- **السبب الجذري:** الـ LiveRouterExecutor يستعمل `/system/script/add + /system/script/run`. الـ parser في وضع السكربت **أصرم** من CLI parser في:
  - معالجة القوائم المفصولة بفواصل غير المُقتبسة.
  - `login-by=http-pap,cookie,mac-cookie` يقبله Terminal، يرفضه script-mode.
- **الحلّ:** Post-processor يلفّ القيم المفصولة بفواصل بعلامات اقتباس:
  ```
  login-by=http-pap,cookie,mac-cookie
  ↓
  login-by="http-pap,cookie,mac-cookie"
  ```
- **Idempotent:** التشغيل المتكرّر لا يضيف اقتباسات مكرّرة.

---

## 5) اختيار المداخل (Hotspot/Broadband interface picker)

### 5.1 الـ payload يكون فارغاً بعد الاختيار
- **المشكلة:** المُشغّل يختار مدخل، لكنّ الـ JSON المرسل ما يحتوي على `interfaces`.
- **مرّ بعدة محاولات:**
  1. `refreshPayload` بعد render — لم يكفِ.
  2. حدث مخصّص `swsvf:configure-collect` يعيد جمع القيم قبل إرسال — حلّ المشكلة.
- **التعزيز:** Filter دفاعي يحذف القيم الفارغة + counter pill يعرض عدد المختارات.

### 5.2 ما في تأكيد بصري للاختيار
- **المشكلة:** المُشغّل يضغط مدخل لكن لا يبدو شيء أنّه «انضمّ».
- **الحلّ:** CSS `:has(input:checked)` على البطاقة → خلفية ملوّنة + حدّ سميك + ✓ في الزاوية.

### 5.3 WAN مقفل من الاختيار
- **المشكلة:** الـ Hotspot/Broadband ينبغي ألّا يستعمل مدخل الإنترنت (WAN) وإلّا يقطع الشبكة على نفسه.
- **الحلّ:** البطاقة الخاصّة بـ WAN مقفلة (`disabled`) مع تنبيه بـ tooltip.

### 5.4 المداخل المُبرمَجة تظهر بحالة «مُختارة» تلقائياً
- **المشكلة:** المُشغّل إذا فتح المعالج لاستبدال إعداد، يحتاج «يعرف» أيّ المداخل عليها إعداد فعلاً.
- **الحلّ:** Pre-fill من حالة الراوتر — البطاقات النشطة تُتأشَّر تلقائياً ولونها مميّز.

### 5.5 الاستبدال يفاجئ المُشغّل
- **المشكلة:** المُشغّل أضاف مدخل 4 → ضاف، أضاف 3 ما ضاف جديد بل استبدل → فاجأه.
- **الحلّ:** قائمة الـ blocked_networks المُحدَّثة من حالة الراوتر + رسالة واضحة «هذا المدخل عليه إعداد سابق، سيُستبدَل».

### 5.6 «اختيار واحد يستبدل، اختيارَين يضيفان»
- **الاكتشاف:** الـ planner كان يضع نفس النطاق `10.20.0.0/24` لكل المداخل، فاختيار مدخل ثاني يخفي الأوّل.
- **الحلّ:** نطاق فريد لكل مدخل: `10.20.<octet>.0/24` حيث `<octet>` مأخوذ من ترتيب المدخل، مع تجنّب أوكتيتات مستعملة من قبل (`used_octets`).

---

## 6) النوافذ المنبثقة (Modals)

### 6.1 Modal الحذف معلّق فوق الصفحة لا يغلق
- **المشكلة:** الـ X والـ overlay click لا يغلقان النافذة.
- **السبب الجذري:** `display:flex` في الـ CSS كان يطغى على خاصيّة `[hidden]` HTML.
- **الحلّ:** قاعدة CSS صريحة:
  ```css
  .modal[hidden] { display: none !important; }
  ```

### 6.2 Modal يفتح افتراضياً
- **المشكلة:** لمّا نضغط «إلغاء» الـ modal لا يختفي.
- **السبب:** نفس مشكلة 6.1 — `display:flex` يقهر `[hidden]`.

---

## 7) NPC (block-sites / walled-garden)

### 7.1 إدخالات NPC تستخدم prefix مختلف
- **المشكلة:** بدلاً من `HOBERADIUS_SETUP:` كانت تستخدم `HOBE_NPC_WEB-BLOCK:<pid>:` و `HOBE_NPC_WALLED-GARDEN:<pid>:`.
- **الحلّ:** Helpers `_is_block_sites_entry()` و `_is_walled_garden_entry()` يتعرّفان على كلا النمطين، وكل واحد فحصه مستقل.

### 7.2 Idempotent Replace يفاجئ المُشغّل
- **المشكلة:** المُشغّل عنده 5 مواقع محجوبة، يفتح المعالج ليضيف 6ـة، يكتب موقع وحيد ويضغط تطبيق → استبدله بالموقع الوحيد بدلاً من الإضافة!
- **الحلّ:** Pre-fill الـ textarea بقائمة المواقع الحاليّة من الراوتر، فالمُشغّل يحرّر في-المكان (يضيف 6ـة للقائمة) بدلاً من البدء من جديد.

---

## 8) Public-IP & Remote-Access

### 8.1 المُشغّل لا يعرف الـ IP الذي يتّصل به
- **المشكلة:** بعد إنشاء وصول عن بُعد، ما في إشارة للـ IP المعروض في Winbox.
- **الحلّ:** قراءة `/ip/cloud` (DDNS المجّاني من MikroTik) → يعطي عنوان `<id>.sn.mynetname.net`. عنوان VPN عبر `nas_devices.address`.
- **العرض:** Banner في «خدماتي» يعرض كلا العنوانين مع أزرار نسخ.

### 8.2 «الوصول عن بُعد ما جابلي IP أصلاً»
- **المشكلة:** قال نجح لكن ما عرض شيء.
- **الحلّ:** بناء حمولة `connection_info` كاملة في response الـ apply + إعادة استخدامها في الـ inventory.

---

## 9) خدماتي (Inventory & Delete UI)

### 9.1 المُشغّل يريد «نظافة» عند الحذف
- **المتطلّب:** «بدي اشيل الهوتسبوت عن مدخل 3» → سكربت تنظيف شامل.
- **الحلّ:** `setup_wizard_v3_router_inventory_remove(router_id)` يحذف الإدخال المحدّد بدقّة باستخدام RouterOS `.id` الداخلي.

### 9.2 «شو من شغل الريدياس وشو يدوي؟»
- **المشكلة:** الحاجة لتمييز الإدخالات التي أنشأها HobeRadius عن تلك التي صنعها المُشغّل يدوياً.
- **الحلّ:** Smart classifier `_classify_source(comment)` + fallbacks بحسب النمط:
  - `_classify_hotspot_source()` ← name pattern `hotspot-<iface>` / `hsprof-<iface>`
  - `_classify_broadband_source()` ← name pattern `hr-pppoe-` / `hr-ppp-profile-`

### 9.3 «بطاقات ملوّنة ظريفة مش ماخذة صفحة»
- **مرّ بأربع تكرارات:**
  1. لائحة في-الاستمارة لكل خدمة (مكرّر، يأخذ مكان).
  2. لائحة موحّدة في صفحة الـ Dashboard (أحسن).
  3. تبويب مستقل «خدماتي» (الأفضل).
  4. كروت صغيرة ملوّنة بترقيم + شارة مصدر + زر حذف (النهائي).

### 9.4 تأكيد الحذف
- **المتطلّب:** «منبسقة + شريط تقدّم».
- **الحلّ:** Modal مع نص تأكيد عربي + progress bar متحرّك أثناء الحذف.

---

## 10) Debug & Diagnostics

### 10.1 صعوبة معرفة سبب فشل الإرسال
- **قبل:** الواجهة تعرض «تعذّر الإرسال» مع stderr غامض، 3 خطوات كلّها ✗ بدون تمييز.
- **بعد:**
  1. **debug_script:** السكربت الكامل المُرسَل يطلع في صندوق `<pre>` تحت رسالة الخطأ لينسخه المُشغّل ويختبر في Terminal.
  2. **Stage-aware progress:** Backend يحدّد المرحلة المُحدّدة التي فشلت (connect / send / commit) ويرجعها في `fail_stage`، الواجهة تعلّم كل خطوة بحالتها الصحيحة:
     - ✓ done (نجحت)
     - ✗ failed (فشلت هنا)
     - − pending (لم نصل لها)
  3. **Console log:** السكربت أيضاً يُكتب في `console.warn` للنسخ السريع من DevTools.

### 10.2 تصنيف نوع الفشل (`_infer_fail_stage`)
- **المنطق:** يفحص `exec_result.error_message + stderr` ضد قوائم patterns:
  - `connect`: `not found in nas_devices`, `ConnectError`, `AuthError`, `timed out`, `connection refused`, ...
  - `send`: `refusing to execute empty`, `exceeds max size`, `script create rejected`, ...
  - `commit`: كلّ شيء آخر (السلوك الافتراضي — حالات MikrotikTrap).

### 10.3 CSS لحالة `pending`
- **الإضافة:** قاعدة في `setup_wizard_v3_router_service_flow.html` لإظهار «—» رمادي للمراحل التي لم نحاول الوصول لها بعد:
  ```css
  .swsvf-apply-substeps li[data-status="pending"] {
    background: #F1F5F9;
    color: #94A3B8;
  }
  .swsvf-apply-substeps li[data-status="pending"]::before {
    content: "— ";
  }
  ```

---

## 11) ملاحظات تشغيليّة للمستقبل

### قائمة فحص قبل إضافة خدمة جديدة
1. تأكّد أنّ خاصيّات RouterOS التي ستستعملها فعلاً موجودة في النسخة المستهدفة 7.20.6.
2. اختبر السكربت في Terminal **و** في `/system/script/add + run` — قد يختلف السلوك.
3. أي قيمة فيها فاصلة (`,`) → اقتبسها (`"value,a,b"`).
4. أي قيمة IP-typed (مثل `local-address`, `dns-server`) → لا تستعمل CIDR ولا قائمة، استعمل قيمة وحيدة فقط.
5. على `/ppp profile` لا تضع `use-radius` — اعمل `/ppp aaa set use-radius=yes` كأمر منفصل.
6. أيّ تعديل على `/ppp profile` بواسطة `set` → استعمل `[find name="..."]`، لا تمرّر الاسم كقيمة.

### قائمة فحص قبل أي post-processor جديد
1. تأكّد أنّ الفئة (Hotspot/Broadband/...) تدعم الخاصيّة التي تريد حقنها.
2. ابحث عن fallback في الـ classifier — لو يستطيع التعرّف بالاسم، التاغ بـ comment غير ضروري.
3. اجعل الـ post-processor **idempotent** — التشغيل المتكرّر لا يخرّب.

### بروتوكول النقاش مع المُشغّل
- لمّا يُبلِّغ عن trap → دائماً اطلب **debug_script + سطر/عمود MikrotikTrap**.
- لمّا يقول «اشتغل في Terminal» → فكّر فوراً في فرق script-parser vs CLI-parser.
- لو ما اشتغل من المحاولة الأولى، ابعث الـ debug_script للمُشغّل ليلصقه بنفسه — هذا أسرع طريق للتشخيص.

---

## 12) سلسلة الـ commits المهمّة (ملخّص زمني)

| Commit | الوصف |
|---|---|
| (early) | Service Flow shell — 4 phases skeleton |
| (early) | 6 services (hotspot, broadband, block-sites, open-sites, public-ip, remote-access) |
| (early) | Consolidate 3 pages → /mt/<id>/dashboard |
| (early) | Collapsible Hub + compact service chips |
| `2869d70` | Hotspot/Broadband: «حاليّاً مفعَّل» panel with per-interface delete |
| `88e126a` | Hotspot/broadband: redesign as compact card grid |
| `1f10724` | New tab: «خدماتي» — unified active-services inventory + delete |
| `a72f4a2` | Drop in-form active-installations panels (moved to «خدماتي» tab) |
| `470f8f2` | Inventory: «عنوان الاتصال» banner above remote-access grants |
| `f9881ea` | Inventory: smarter source classification for hotspot + broadband |
| `1bdcafe` | Hotspot: surface debug_script in apply-error banner |
| `82138a3` | Wizard: stage-aware failure progress (connect/send/commit) |
| `e56507d` | Hotspot: split comment injection into separate `set` lines |
| `754487e` | Hotspot: do not tag /ip hotspot profile (no comment field on 7.20.6) |
| `2bccbe3` | Hotspot: drop comment tagging entirely (no comment field on hotspot at all) |
| `f1f9525` | Hotspot: quote `login-by` comma-list to survive script-mode parser ← **حلّ ترَب Hotspot نهائياً** |
| `5e17e86` | Docs: comprehensive lessons-learned for Setup Wizard v3 |
| `69f5e1e` | Broadband: scope cleanup per-interface (fix replacement bug) ← **حلّ مشكلة الاستبدال** |

---

## 13) خاتمة

كل ما ذُكر أعلاه نتاج تشخيص مباشر على راوتر حقيقي يشغّل RouterOS 7.20.6.
السلوك قد يختلف على نسخ أخرى — لو ظهرت مشكلة جديدة في نسخة مختلفة:
1. استخرج الـ debug_script بالكامل.
2. حدّد السطر/العمود من رسالة `MikrotikTrap`.
3. اختبر في Terminal للتأكّد إن كان فرق parser.
4. أضف سطر/فقرة جديدة لهذا الملف.

> **آخر تحديث:** 2026-05-27 — بعد commit `69f5e1e` الذي حلّ مشكلة
> استبدال الـ Broadband. الـ Broadband صار additive: إضافة مدخل
> جديد لا تمسح المداخل القائمة. الـ Hotspot كذلك سليم بفضل
> per-interface tags الموجودة في الـ planner.
