# جرد الميزات الوهمية/العرض-فقط/المرتبطة بالطرفية — radius-module
**نوع المهمة:** قراءة-فقط (لم يُعدَّل أي ملف مصدري). **اللقطة:** `origin/main bfb568d` عبر worktree معزول.
**الطريقة:** 9 وكلاء قراءة-فقط (Explore) + تتبّع يدوي مباشر للأسئلة المحورية. **التاريخ:** 2026-06-10.

---

## ملخّص تنفيذي — إجابات الأسئلة المحورية (6/7/8)

| السؤال | الإجابة المُتتبَّعة |
|---|---|
| **6) إخفاء قسم: مُنفَّذ خادميًا (403) أم سايدبار فقط؟** | **مختلط.** إخفاء قائم على صلاحيات RBAC = **مُنفَّذ خادميًا فعلاً**: `blueprint.py:534 _perm_guard` يفرض `_NAV_PERM` على GET ويرفع `abort(403)` للوصول المباشر بالعنوان لغير السوبر (+ حارس الكتابة `_PERM_GUARDED`). **لكن** أقسام مُخفاة بتعليق السايدبار يدويًا (عمليات الشبكة، دفع DHCP، الإعداد الهندسي) **مساراتها تبقى مفتوحة بلا 403** (إلا ما له حارس خاص super_admin). 4 مسارات مستثناة عمدًا من حارس العرض (`_NAV_VIEW_GUARD_SKIP`: audit/mt_alerts/hotspot_errors/cards_checker) لأن لها `requires_perm` خاص. |
| **7) تعطيل/تجميد قسم: يتوقف فعلًا end-to-end أم بصريًا؟** | **مختلط.** العمليات التدميرية مُعطَّلة فعليًا (NPC apply = `NullRouterExecutor` يرفض؛ device-health/site-exit/mt-restore = NO-OP/مقفل). التحصيل «مجمّد»: مسارات `payment_collection.py` تَصدّ بـ `_frozen()` خادميًا، **لكن** هاب `finance_collection` وبعض المسارات مفتوحة بصريًا فقط. القاعدة: التدمير مؤمَّن، التنقّل غير متّسق. |
| **8) تكامل اللوحة↔RADIUS: وهمي أم حقيقي؟** | **RADIUS حقيقي.** `sqlite_adapter` يكتب/يقرأ `radacct`/radcheck فعلاً (يستهلكه FreeRADIUS rlm_sql)؛ `radius_coa.py` يرسل CoA/Disconnect حقيقيًا (RFC 5176 عبر UDP). `freeradius_translator` يزامن radcheck/radreply/radusergroup. **جسر اللوحة↔الترخيص ليس بيانات وهمية بل «عرض/جاف»**: heartbeat/usage/restore/activation كلها dry-run في الواجهة، والإرسال الفعلي عبر workers مُفعَّلة بأعلام env. |

### تجميع حسب التصنيف (تقديري)
- **mock-data (بيانات وهمية تُعرض كحقيقية):** مختبر الدفع بالكامل + بوابات الدفع الوهمية + موزّعو/عروض الهوتسبوت الافتراضية + روابط متجر placeholder + معاينات الطباعة (SAMPLE/CARD1234).
- **terminal-only (يُضبط فقط من env/CLI — يخالف قاعدة «كله من اللوحة»):** ~22 مفتاحًا (أكبر فئة) — مفاتيح WireGuard/IP العام/مجلد النسخ/رابط الهوتسبوت/أعلام الجسر/device-health apply+poll/تفعيل التحصيل/RADIUS_MODE/MIKROTIK_*/علم استعادة/رفع محتوى النسخ/توكنات API.
- **stub-endpoint / 501 / not_implemented:** GDrive backup، تنزيل ملفات MT، عقود API، استعادة الترخيص، callback أحداث الجسر، تغيير IP العام، RadiusPolicy CRUD.
- **display-only / dry-run:** جسر اللوحة (heartbeat/usage/backup/restore/activation)، إجراءات الحملات، أعلام البوابة غير المربوطة، أزرار apply المؤجّلة.
- **partial-todo:** فحوص الأجهزة الدورية (Sprint 2+)، تنفيذ الحملات، شراء/تغيير باقة ذاتي بالبوابة، jobs متزامنة بلا worker.

---

## أ) المدفوعات والمال — أخطر منطقة (وهمية بالكامل)
| الميزة | الموقع | لماذا ليست حقيقية | المطلوب لتصبح حقيقية ومن الواجهة | الجهد |
|---|---|---|---|---|
| مختبر الدفع `/payments-lab` + `/pay-demo` | `routes/payments_lab.py:1-248` | محاكاة كاملة؛ `DEMO_ITEMS`/`UI_PROVIDERS` ثابتة؛ لا ترحيل محاسبي إطلاقًا (التعليق صريح) | بوابات حقيقية + ترحيل عبر `AccountingService` عند الدفع | **NEEDS-OWNER-INPUT**: أي بوابة أولًا (jawwal_pay/esadad/lahza/palpay)؟ اتفاقيات+اعتمادات |
| `MockWalletProvider` (`is_mock=True`) | `services/payment_providers/__init__.py:75-243` | OTP يُولَّد وينسخ في `meta['demo_otp']` للعرض؛ `confirm_otp` يعلّم paid بلا مال | فئات مزوّد حقيقية + HMAC/webhook حقيقي + خادم SMS | **NEEDS-OWNER-INPUT** |
| بطاقات اعتماد المزوّدين | `routes/payments_lab.py:77-84` + `templates/.../payments_lab.html:177-201` | حقول `disabled` «تُفعَّل بعد توقيع الاتفاقيات» | مسار حفظ اعتمادات + تفعيل مزوّد في DB | **NEEDS-OWNER-INPUT** |
| `PAYMENT_PROVIDERS` خياران فقط | `db/repos/payments_repo.py:24` | فقط `manual_wallet`+`jawwal_pay`؛ esadad/palpay/lahza/bop غير مسجّلة | تسجيلها في `REGISTRY` | **NEEDS-OWNER-INPUT** |
| تجميد التحصيل | `routes/finance_collection.py:21-45` | يُفك فقط عبر `HOBERADIUS_COLLECTION_FORCE_OPEN=1` (env) أو إعداد بوابة jawwal_pay/api | toggle تفعيل التحصيل بالواجهة + بوابة | **DOABLE-NOW** (toggle) / مدخل: سياسة الإنتاج |
| `api/v1/payments.py` أساس جزئي | `app/api/v1/payments.py` | «foundation» — لا تقسيم فواتير ولا تجديد تلقائي | محرّك دفع + تجديد + إعادة محاولة + استرداد | **NEEDS-OWNER-INPUT** |

## ب) جسر اللوحة ↔ الترخيص (عرض/جاف — لا تنفيذ من الواجهة)
| الميزة | الموقع | لماذا | المطلوب | الجهد |
|---|---|---|---|---|
| تقارير الاستخدام / heartbeat / حالة الترخيص | `routes/admin_bridge.py:90-160` | «وضع جاف» — تجميع/عرض محلي، لا POST بعيد من الصفحة؛ الإرسال عبر worker مفصول | تشغيل/جدولة الـworkers + زر «مزامنة الآن» بالواجهة | **DOABLE-NOW** |
| رفع النسخ / الاستعادة / تفعيل الخدمات | `routes/admin_bridge.py:102-118` | «غير مفعّل إنتاجيًا»؛ adapters dry-run | مدخل المالك: هل يُفعَّل من الواجهة أم يبقى worker/CLI؟ | **NEEDS-OWNER-INPUT** |
| `apply_restore()` | `services/license_admin_restore.py:194-199` | يُرجع `not_implemented` (`restore_apply_not_implemented_in_p08`) + يتطلب `HOBERADIUS_ADMIN_RESTORE_APPLY_ENABLED=1` | تنفيذ استعادة SQLite فعلية + توقيت/checksum | **NEEDS-OWNER-INPUT** |
| تغيير IP العام | `services/license_admin_public_ip_change.py:1-77` | `dry_run_completed` فقط؛ live = `public_ip_change_live_apply_not_enabled` | executor يفتح MT ويطبّق NAT/route | **NEEDS-OWNER-INPUT** |
| callback أحداث الجسر | `services/license_admin_bridge_events.py:127-133` | `not_configured` — تُسجَّل محليًا ولا تُرسل | تفعيل webhook endpoint باللوحة | **NEEDS-OWNER-INPUT** |
| رفع محتوى النسخ | `services/license_admin_backup_upload.py:163-173` | metadata_only افتراضيًا؛ المحتوى يتطلب علم env | toggle بالواجهة | **NEEDS-OWNER-INPUT** |
| أعلام الجسر (runtime/identity sync/worker) | `routes/admin_bridge.py:172-175`, `auth.py:62` | `HOBERADIUS_ADMIN_*` env فقط، لا UI | قسم إعدادات بالواجهة + toggles | **NEEDS-OWNER-INPUT** |

## ج) RADIUS الأساسي (حقيقي — مع micro-stubs)
| الميزة | الموقع | الحالة | الجهد |
|---|---|---|---|
| CoA/Disconnect + FreeRADIUS translator + radacct | `radius_coa.py`, `freeradius_translator.py`, `sqlite_adapter.py:181-396` | **حقيقي** (يتطلب FreeRADIUS مثبّت + nas_devices صحيح) | DOABLE-NOW (تشغيلي) |
| `RADIUS_MODE` اختيار الـadapter | `integration/factory.py:32-38` | terminal-only (env)، الافتراضي sqlite (إنتاج) | **NEEDS-OWNER-INPUT**: UI أم deployment-config؟ |
| `MIKROTIK_*` اتصال الراوتر | `integration/mikrotik/settings.py:46-63` | terminal-only؛ in-memory، لا UI/DB، يتطلب restart | **DOABLE-NOW**: جدول `mikrotik_connections` + CRUD/test بالواجهة |
| `RadiusPolicy` CRUD | `sqlite_adapter.py:433-442` | stub: list يرجع []، upsert بلا حفظ، delete no-op | DOABLE-NOW |
| `list_accounting` (MT adapter) | `mikrotik_adapter.py:278-280` | يرجع [] دائمًا (التجميع من radacct متاح) | DOABLE-NOW |
| مسارات MT القديمة | `routes/integrations.py:26-86` | 410 Gone مقصود (مُرحَّلة للـwizard) | DOABLE-NOW (تنظيف) |

## د) عمليات الشبكة وصحة الأجهزة (التطبيق الحي مقفول بأعلام/مراحل)
| الميزة | الموقع | لماذا | الجهد |
|---|---|---|---|
| Device Health Live Apply | `services/device_health.py:500-582`, `device_health_mikrotik.py:112` | **بوابة مزدوجة**: env `HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY` **و** toggle الواجهة؛ env يقدر يقفل رغم الواجهة | **NEEDS-OWNER-INPUT**: هل يُزال علم env (تحكّم من الواجهة فقط)؟ |
| Device Health Poll (خلفي) | `services/device_health_poller.py:210-219` | الاستطلاع المستمر يتطلب `HOBERADIUS_DEVICE_HEALTH_POLL` (env)؛ الزر اليدوي يعمل | DOABLE-NOW (توثيق/UI) |
| NPC / Network-Policy Apply | `routes/network_policy.py:1059-1199`, `npc_apply_service.py` | `NullRouterExecutor` يرفض كل تطبيق (مقصود حتى Phase 5)؛ كل apply يُسجَّل لكنه NO-OP | **NEEDS-OWNER-INPUT**: استراتيجية التنفيذ (API/queue/canary) |
| Port Script Services apply | `routes/port_script_services.py:365-469` | خدمات placeholder تَصدّ؛ الحقيقية تتطلب push scheduler من `mt_programming` | DOABLE-NOW / مدخل: scripts الخدمات الجديدة |
| فحص الأجهزة/مسح IP | `routes/network_devices.py:238`, `network_ip_scan.py:53` | يدوي فقط (TCP probe / dhcp-print)؛ لا جدولة/تنبيهات (Sprint 2+) | DOABLE-NOW (لاحقًا) |
| استعادة نسخ MT | `routes/mt_backups.py:235-275` | زر معطّل «مقفل لحماية الراوتر» | **NEEDS-OWNER-INPUT**: مراسم الاستعادة |
| snapshot قبل التطبيق | `routes/device_health.py:174-177` | `StateReaderNotConfigured` (snapshot_id=None) | NEEDS-OWNER-INPUT: backend القارئ |

## هـ) الهوتسبوت/المتجر/البطاقات/الطباعة (بيانات عرض افتراضية + اعتماد إعداد IP)
| الميزة | الموقع | لماذا | الجهد |
|---|---|---|---|
| موزّعو/عروض افتراضية | `services/hotspot_templates.py:287-303, 363-372` | قوائم ثابتة (محل الاتصالات المركزي/باقة الألعاب…) تُعرض في المعاينة والمتجر | **NEEDS-OWNER-INPUT**: ربط بموزّعي/باقات السوق الحقيقية |
| رابط متجر placeholder | `hotspot_templates.py:236-268, 340` | `192.168.88.2/portal/card` افتراضي حتى ضبط `network.radius_server_ip` (أو env `HOBERADIUS_PUBLIC_IP`) | **NEEDS-OWNER-INPUT**: ضبط IP من الإعدادات |
| نشر store.html للراوتر | `services/hotspot_store_page.py:296-360` | يفشل بلطف إن MT/FTP غير متاح | NEEDS-OWNER-INPUT (اعتمادات MT) |
| walled-garden | `hotspot_store_page.py:142-196` | يحاول API ثم يعطي أمرًا للصق اليدوي عند فشل الصلاحية | NEEDS-OWNER-INPUT |
| معاينات الطباعة SAMPLE/CARD1234 | `routes/print_templates.py:790-880` | بيانات وهمية في المعاينة (بانتظار اختيار دفعة حقيقية) | DOABLE-NOW |
| صفحات رفقة الهوتسبوت | `services/hotspot_companion_pages.py` | قوالب `$(...)` يملؤها RouterOS وقت التشغيل (طبيعي) | DOABLE-NOW |

## و) الاتصالات (معماري سليم لكن إجراءات معلّقة)
| الميزة | الموقع | لماذا | الجهد |
|---|---|---|---|
| إجراءات الحملات (wallet_credit/add_free_days) | `services/notification_campaigns.py:547-563` | تُعرض كـUI لكن `dry_run_only` دائمًا — لا تنفيذ | **NEEDS-OWNER-INPUT**: workflow الموافقة/التنفيذ |
| تنفيذ/موافقة الحملة | `routes/communications.py:225-249` | تُنشأ `dry_run_ready` بلا زر «إرسال» | **NEEDS-OWNER-INPUT** |
| `QueuedOnlyProvider` افتراضي | `notification_campaigns.py:82-93` | عند تعطيل القناة يظهر «queued» وكأنه أُرسل (يخدع المشغّل) | DOABLE-NOW (شارة «لم يُرسل») |
| بوابات WhatsApp لكل حدث | `routes/whatsapp.py:29-36` | toggles معروضة لكن الفرض في `notifications_engine` لا هنا | DOABLE-NOW |
| `balance_url` | `services/comms_providers.py:138,156` | مخزّن ويُعرض لكن غير مُستخدَم إطلاقًا | NEEDS-OWNER-INPUT (ميزة/dead) |
| webhook البوت يرجع 200 دومًا | `routes/communications.py:401-422` | يخفي الأخطاء الحقيقية | DOABLE-NOW |
| Telegram للأحداث | `notifications_engine.py:35-38` | يُشير لـ`telegram_notifier` غير مؤكّد التنفيذ | NEEDS-OWNER-INPUT |

## ز) بوابة المشترك والتقارير
| الميزة | الموقع | لماذا | الجهد |
|---|---|---|---|
| أعلام البوابة 3/9 غير مربوطة | `routes/customer_portals.py:82-85` | `allow_password_change/self_purchase/plan_change` مخزّنة «قيد الربط» بلا أثر | password_change=**DOABLE-NOW**؛ self_purchase/plan_change=**NEEDS-OWNER-INPUT** (تتطلب دفع) |
| 6 أعلام عرض البوابة | `routes/customer_portals.py:75-81` | **مُنفَّذة بأمان** (فحص 403 بكل route) — لا عمل مطلوب | — |
| `HOBERADIUS_HOTSPOT_LOGIN_URL` / `WG_SUBNET` | `customer_portals.py:225`, `status.py:166` | env فقط، لا UI | DOABLE-NOW (نقل لإعدادات) |
| أرشيف التقارير / كاش الصحة 30s | `dashboard_reports.py`, `dashboard_metrics.py:157` | immutable/مؤخّر بالتصميم (ليست أرقامًا وهمية) | — |

## ح) الإعداد/SaaS/الأدوات/التراخيص — أعلام env كثيرة + 501
| الميزة | الموقع | لماذا | الجهد |
|---|---|---|---|
| WireGuard (IP/endpoint/pubkey) | `setup_wizard_v3.py:438-457, 3079-3085` | env فقط، لا UI، يتطلب redeploy | **NEEDS-OWNER-INPUT**: تدوير المفاتيح؟ |
| IP العام / مجلد النسخ / توكنات API | `mt_setup.py:62-68`, `mt_backups.py:55`, `mt_dashboard.py:55-62` | env فقط؛ توكنات CSV اختصار تطوير | DOABLE-NOW (إعدادات بالواجهة) |
| استعادة محلية | `routes/backups.py:41-44` | تُعطَّل بـ`HOBERADIUS_LOCAL_RESTORE_DISABLED`؛ المسار يبقى مسجّلًا (لا 403 خادمي) | DOABLE-NOW (حارس خادمي + toggle) |
| GDrive backup / تنزيل ملفات MT / عقود API | `api/v1/backups.py:42`, `mikrotik_control.py:928`, `api/v1/contracts.py:15` | 501 Not Implemented | NEEDS-OWNER-INPUT (roadmap) |
| Site-exit apply | `routes/site_exit.py:271-273` | معطّل عمدًا VX2.4 (زر يوحي بعمل) | NEEDS-OWNER-INPUT (إخفاء أم تفعيل VX2.6؟) |
| jobs متزامنة | `routes/jobs.py:63-91` | لا worker خلفي بعد | NEEDS-OWNER-INPUT |
| حدود الـtiers (المستأجرون) | `api/v1/tenants.py:133` | تُعرض بلا فرض فعلي | NEEDS-OWNER-INPUT (hard/soft cap) |
| أدوات الصيانة/التعديلات/اختبار المصادقة | `api/v1/tools.py:168-425` | أنماط dry-run/confirm-token آمنة (تشخيصية) | DOABLE-NOW (UI لاحقًا) |

---

## ملاحظات نطاق وتحذيرات
- **لم يُعدَّل أي ملف مصدري.** الجرد ضد لقطة ثابتة `bfb568d` في worktree معزول؛ وكلاء آخرون يعدّلون الشجرة الأساسية بالتوازي فقد تتغيّر الأرقام/الأسطر.
- أكبر مكسب لقاعدة «كله من اللوحة»: **نقل ~22 مفتاح env إلى إعدادات قاعدة بيانات بواجهة** (WireGuard/IP/النسخ/الجسر/device-health/التحصيل/RADIUS_MODE/MIKROTIK_*).
- أخطر «وهمي حقيقي»: **منظومة الدفع بالكامل** — تحتاج قرار مالك (مزوّد + اتفاقية) قبل أي بناء.
- توصية أمنية: توحيد سياسة الإخفاء مع الحراسة الخادمية — كل بند مُخفى من السايدبار يجب أن يقابله حارس 403/404 خادمي (عمليات الشبكة، DHCP push، الاستعادة المحلية، التحصيل المجمّد).
