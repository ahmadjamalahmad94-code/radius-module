# تتبع حالة الأجهزة — Network Device Health Monitor

> **Phase 0 — Audit & Design.** يوثّق هذا الملف نتيجة تدقيق المعمارية القائمة
> والقرارات التصميمية للميزة الجديدة، قبل كتابة أي كود يلمس MikroTik حيًّا.
> لا توجد أي طفرات (mutations) حيّة على الراوترات في المرحلتين 1 و2.

تاريخ التصميم: 2026-06-09 · النطاق: `radius-module` فقط.

---

## 1. الهدف

قسم مستقل داخل `radius-module` لمراقبة أجهزة الشبكة (Access Points، روابط،
UniFi، LiteBeam، سويتشات، أي جهاز يحمل IP) خلف راوترات MikroTik. ليس مجرد
جدول — بل يدير التدفّق التشغيلي الكامل:

1. تسجيل أجهزة الشبكة (الاسم/النوع/الراوتر/المدخل/IP/المكان).
2. تجهيز وصول MikroTik لشبكة الجهاز: إضافة IP/Gateway على المدخل،
   إضافة Hotspot IP-Binding (bypass) للـsubnet عند اللزوم، إضافة/تحديث
   Netwatch host لعنوان الجهاز.
3. استطلاع Netwatch و/أو نتيجة ping من MikroTik وعرض الحالة الدقيقة داخل
   HobeRadius.
4. إرسال تنبيهات عبر قنوات الإشعار القائمة عند هبوط جهاز أو ارتفاع البنج.

المسار: `GET /admin/radius/device-health` — تسمية السايدبار: **تتبع حالة الأجهزة**.

---

## 2. تدقيق المعمارية القائمة (نتائج Phase 0)

### 2.1 المسارات والـBlueprint
- Blueprint واحد `radius` بـ`url_prefix="/admin/radius"`
  (`app/radius/routes/blueprint.py`). كل وحدة مسارات تُسجَّل عبر
  `register_*_routes(bp)` داخل `_register_all()`.
- الحراسة عبر `before_request` عامّين: `_install_global_login_guard`
  (يتطلب تسجيل الدخول لكل ما هو غير `_PUBLIC_ENDPOINTS`) و
  `_install_permission_guard` (يطبّق `_PERM_GUARDED`). **السوبر أدمن يمرّ دائمًا**
  عبر `session["is_super_admin"]`.
- نقاط غير مذكورة في `_PERM_GUARDED`/`_NAV_PERM` = مفتوحة لأي مسؤول مُسجّل.
  لذا تكفي حماية تسجيل الدخول لمرحلتنا الأولى؛ يُضاف مفتاح صلاحية مخصّص لاحقًا.

### 2.2 CSRF
- يُحقن `_csrf_token` تلقائيًا في كل `<form method=post>` (after_request).
- نقاط `/api/*` فقط مُعفاة من CSRF. مساراتنا تحت `/admin/radius/device-health/...`
  **ليست** معفاة → كل POST/PATCH يجب أن يحمل ترويسة `X-CSRFToken`.
  القيمة متاحة في `<meta name="csrf-token">` ضمن `_admin_layout.html`.

### 2.3 قاعدة البيانات والهجرات
- SQLite، WAL، `foreign_keys=ON`. هجرات SQL خام مرقّمة في
  `app/radius/db/migrations/NNN_*.sql`، تُطبَّق تلقائيًا عند الإقلاع عبر
  `migrations_runner.run_pending_migrations()`. **أعلى رقم حالي = 114** →
  نستخدم **115**.
- اتصال عبر `db()` / `transaction()` في `db/connection.py`. نمط repo لكل جدول.
- اصطلاحات: `tenant_id INTEGER NOT NULL DEFAULT 1` في كل جدول؛ طوابع
  `created_at`/`updated_at TEXT DEFAULT (datetime('now'))`؛ حذف ناعم عبر
  `deleted_at/deleted_by/delete_reason` للكيانات المهمّة.
- `now_iso()` من `db/helpers.py` يكتب ISO‑8601 بلاحقة Z.

### 2.4 تكامل MikroTik (موجود — نعيد استخدامه)
- عميل سلكي منخفض المستوى `integration/mikrotik/client.py` + تجميع اتصالات
  `integration/mikrotik/pool.py`.
- **غلاف إداري** `services/mikrotik_admin_client.py` (mac):
  - `_safe_dial(nas=, operation=, work=)` يشغّل `work(client)` ويحوّل كل خطأ
    إلى `MtResult(ok=False, error=…)` — لا يرمي استثناءً للطبقة العليا.
  - قراءات جاهزة: `ip_addresses()` (`/ip/address/print`)، وكثير غيرها.
  - `tool_ping(nas, target=, count=)` → `MtResult` (صفّ لكل حزمة + ملخّص).
  - `client.print_(path)` يُرجِع صفوف `!re` فقط؛ `client.run(path, attrs=)`
    لعمليات الكتابة.
- `nas_repo.get_nas(tenant_id, nas_id)` يُرجِع `NasDevice`؛ نحوّله لـdict للغلاف.
- **سابقة Netwatch**: `services/router_netwatch_planner.py` يثبّت
  `/tool/netwatch` بتعليق `HOBE_NETWATCH:<device_id>:…` (للميزة القديمة
  «تابع أجهزة الشبكة»). نتبع نفس فلسفة «التعليق المُدار» لكن ببادئة خاصّة بنا
  حتى لا نتداخل: `managed-by device-health device_id=<id>`.

### 2.5 الإشعارات والتدقيق
- `services/notifications_engine.notify_event(event_key, tenant_id=, context=)`
  → يوزّع على Telegram/SMS/WhatsApp حسب قواعد المستأجر. لا يرمي أبدًا.
- `telegram_notifier.send_to_tenant(tenant_id, text)`.
- `services/audit.get_audit_service().record(actor=, action=, target_type=,
  target_id=, router_id=, severity=, result_status=, before=, after=)`.

### 2.6 القوالب والأصول
- القاعدة `admin/_admin_layout.html`؛ مكتبة ماكرو `_partials/hub.html`
  (`megahero`, `worklayout`, `helppanel`, `section`, `kpi`, `pill`, `btn`,
  `modal`, `field`).
- `unified_design.css/js` مُحمّلان عالميًا — مودالات `data-uds-modal-open`/
  `data-uds-modal-close` تعمل تلقائيًا.
- المصدر اللغوي عربي؛ النصوص داخل `{{ _('…') }}` لاستخراج gettext.

### 2.7 تعارض الأسماء — قرار مهم
يوجد ملف خدمة **قائم** `services/network_device_monitor.py` يخصّ الميزة
القديمة «تابع أجهزة الشبكة» (cron TCP-probe على جدول `network_devices`).
لتفادي الاصطدام:
- **وحدات Python للميزة الجديدة تأخذ بادئة `device_health_*`**.
- **أسماء الجداول تبقى `network_device_monitor_*`** (حسب المواصفة) — لا تصادم
  في قاعدة البيانات (تأكدنا أنها غير موجودة).

---

## 3. نموذج البيانات (Migration 115)

كلها بادئة `network_device_monitor_`. كلها `tenant_id` + طوابع زمنية.

### `network_device_monitor_devices`
السجل الرئيسي. حذف ناعم. **منع التكرار**: فهرس فريد جزئي على
`(tenant_id, router_id, ip_address)` حيث `ip_address<>'' AND deleted_at IS NULL`.
الحقول: `router_id, name, device_type, interface_name (إلزامي), ip_address,
network_cidr, gateway_address, location, subnet_prefix(=24), gateway_last_octet(=254),
ping_threshold_ms(=80), netwatch_interval_sec(=60), netwatch_timeout_sec(=3),
alert_channel, monitoring_enabled(=1), status(=unknown), last_latency_ms,
last_checked_at, last_status_change_at, last_down_at, last_up_at,
consecutive_down_count, consecutive_high_latency_count, mikrotik_netwatch_id, notes`.

تعداد `status`: `up | down | timeout | high_latency | unknown | disabled | apply_failed`.

### `network_device_monitor_network_scopes`
نطاق الشبكة لكل **(router_id + interface + network_cidr)** — المفتاح المركّب
الحرج. فريد على `(tenant_id, router_id, interface_name, network_cidr)`.
نفس الـsubnet على مدخل آخر = سجل نطاق منفصل **+ تحذير غموض توجيه**.
الحقول: `gateway_address, mikrotik_address_id, apply_status(=pending), last_applied_at`.

### `network_device_monitor_bindings`
IP‑Binding (bypass) لكل `(router_id + network_cidr + binding_type)`.
الحقول: `binding_type(=bypassed), mikrotik_binding_id, apply_status, last_applied_at`.

### `network_device_monitor_events`
`device_id, event_type, previous_status, new_status, latency_ms, message, created_at`.

### `network_device_monitor_alerts`
`device_id, alert_type, channel, status, sent_at, dedup_key, message, created_at`.

تعداد `apply_status` (للنطاقات/الـbindings):
`pending | already_present | applied | apply_failed`.

---

## 4. منطق الشبكة (مثال المواصفة)

جهاز IP `192.168.15.10` على `ether2`، بادئة 24، آخر أوكتت للبوابة 254:
- `network_cidr = 192.168.15.0/24`
- `gateway_address = 192.168.15.254/24`

الحساب عبر `ipaddress` القياسية: `network_address(net) + gateway_last_octet`،
مع التحقق أنه داخل النطاق وأن البادئة 1..32 وأن الـIP صالح IPv4.

خطة MikroTik المقصودة (Phase 2 diff، Phase 3 apply):
- `/ip/address add address=192.168.15.254/24 interface=ether2 comment="managed-by device-health"`
- `/ip/hotspot/ip-binding add address=192.168.15.0/24 type=bypassed comment="managed-by device-health"`
- `/tool/netwatch add host=192.168.15.10 type=simple interval=.. timeout=.. comment="managed-by device-health device_id=<id>"`

---

## 5. منع التكرار (قبل أي apply)

| العنصر | مفتاح الكشف | عند الوجود |
|--------|-------------|------------|
| الجهاز | router + ip_address | يُرفض الإنشاء (خطأ عربي) |
| IP Address | router + interface + address/prefix أو network scope | `already_present` |
| IP Binding | router + subnet/address + bypass type | `already_present` |
| Netwatch | router + host IP | `already_present` |

نفس الـsubnet على أكثر من مدخل → يُسمح كنطاق منفصل **مع تحذير** (غموض توجيه؛
نستعمل Netwatch `src-address` إن دعمه RouterOS المنشور).

---

## 6. معمارية الخدمات (ملفات الميزة)

- `services/device_health.py` — منطق الأعمال، CRUD، انتقالات الحالة.
- `services/device_health_planner.py` — حساب الشبكة/البوابة، الخطة المقصودة،
  الـdiff والـdry-run، منع التكرار.
- `services/device_health_mikrotik.py` — غلاف رقيق فوق `mikrotik_admin_client`:
  قراءة/إضافة address، قراءة/إضافة ip-binding، قراءة/إضافة/تحديث netwatch،
  ping. (Phase 2 = قراءة + ping فقط؛ دوال الكتابة موجودة لكن **غير مُوصّلة
  بأي مسار** حتى Phase 3.)
- `services/device_health_poller.py` — Phase 4 (لاحقًا).
- `routes/device_health.py` — ويب + JSON.
- `templates/radius/device_health.html`, `static/css/device_health.css`,
  `static/js/device_health.js`.
- `db/repos/device_health_repo.py`.

---

## 7. المسارات

- `GET  /admin/radius/device-health` — الصفحة.
- `GET  /admin/radius/device-health/api/devices` — قائمة JSON (+ملخّص).
- `POST /admin/radius/device-health/api/devices` — إنشاء.
- `PATCH /admin/radius/device-health/api/devices/<id>` — تحديث.
- `POST /admin/radius/device-health/api/devices/<id>/enable|disable|delete`.
- `POST /admin/radius/device-health/api/devices/<id>/sync` — Phase 2: يقرأ
  حالة الراوتر ويُعيد الخطة (already_present/create) — **بلا طفرة**.
- `POST /admin/radius/device-health/api/devices/<id>/test-ping` — ping تشخيصي.
- `GET  /admin/radius/device-health/api/plan?router_id=&interface=&ip=&...` —
  معاينة الخطة (محسوبة، بلا اتصال راوتر في Phase 1).

---

## 8. المراحل والحالة

- **Phase 0** ✓ — هذا الملف.
- **Phase 1** — DB + UI + CRUD + حساب الشبكة + منع تكرار الجهاز + معاينة خطة
  محسوبة (dry-run). بلا طفرة. اختبارات الحساب والتحقق والـrepo.
- **Phase 2** — قارئ MikroTik + diff خطة idempotent (already_present/create) +
  `sync`/`test-ping`. بلا طفرة حيّة. اختبارات بعميل MikroTik مُموّه.
- **Phase 3+** — apply حيّ مُتحكَّم، استطلاع، تنبيهات (خارج هذا التسليم).

## 9. ملاحظة التراجع (Rollback)
لا تراجع تدميري تلقائي. Phase 3 ستضيف فقط العناصر المفقودة بتعليق
`managed-by device-health`؛ الإزالة عند الطلب الصريح فقط، وتمسح صفوفنا
المعلّمة دون لمس إعدادات المشغّل الأخرى.
