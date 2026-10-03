"""مصدر الحقيقة الموحّد لتعريب قيم «مركز الأحداث» (business_events).

صفحة «قائمة الأحداث» (`/admin/radius/events`) تعرض قيمًا مخزّنة خامًا
بالإنجليزية: مفتاح الحدث (`event_key`)، نوع الهدف (`target_type`)،
الفئة (`category`)، ونوع الفاعل (`actor_type`). موجة التدويل (i18n)
لم تغطِّها لأنها بيانات مخزّنة لا نصوص قوالب.

هذا الملف يحوّلها إلى عربية مقروءة عبر:
  1. خريطة دقيقة (exact map) للمفاتيح/الأنواع المعروفة.
  2. مُركِّب تلقائي (composer) للمفاتيح غير المعرّفة: «فِعل + اسم القسم».
  3. تأنيس أخير (humanize): استبدال «.» و«_» بمسافات وأخذ المقطع الأخير
     — حتى لا تظهر خانة فارغة أبدًا.

القيمة الخام تبقى متاحة دائمًا في `title` بالقالب للمرجع الفنّي.
"""
from __future__ import annotations
from app.i18n_text import N_

# ── 1) خريطة دقيقة لمفاتيح الأحداث المعروفة (event_key → عربي) ──
EVENT_KEY_LABELS: dict[str, str] = {
    # المحفظة
    "wallet.created":    N_("إنشاء محفظة"),
    "wallet.credit":     N_("إضافة للمحفظة"),
    "wallet.debit":      N_("خصم من المحفظة"),
    "wallet.frozen":     N_("تجميد المحفظة"),
    "wallet.unfrozen":   N_("إلغاء تجميد المحفظة"),
    "wallet.closed":     N_("إغلاق المحفظة"),
    "business_os.wallet.credit": N_("إضافة للمحفظة"),
    "business_os.wallet.debit":  N_("خصم من المحفظة"),
    # أحداثُ النظامِ والتنبيهات — كانت تسقط إلى التأنيسِ الأخير فتظهر «offline»
    # «online» «limit» إنجليزيّةً خامّة في مركزِ الأحداث (r6ui)
    "router.offline":      N_("انقطاع راوتر"),
    "router.online":       N_("عودة راوتر للاتصال"),
    "debt.limit":          N_("تجاوز حدّ الدَّين"),
    "subscriber.expiring": N_("اشتراك يقترب من الانتهاء"),
    "batch.low":           N_("حزمة بطاقات توشك على النفاد"),
    "backup.done":         N_("اكتمال نسخة احتياطية"),
    "worker.failed":       N_("فشل مهمّة خلفيّة"),
    # المتجر — إيداع / سحب / شات / تسجيل / حزمة
    "store.deposit_requested":   N_("طلب إيداع"),
    "store.deposit_confirmed":   N_("تأكيد إيداع"),
    "store.deposit_rejected":    N_("رفض إيداع"),
    "store.withdrawal_requested": N_("طلب سحب"),
    "store.withdrawal_confirmed": N_("تأكيد سحب"),
    "store.withdrawal_rejected":  N_("رفض سحب"),
    "store.chat.customer_message": N_("رسالة عميل في المتجر"),
    "store.chat.admin_message":    N_("رد المتجر على العميل"),
    "store.registration":          N_("تسجيل في المتجر"),
    "store.package_purchased":     N_("شراء حزمة من المتجر"),
    "store_chat.customer_message": N_("رسالة عميل في شات المتجر"),
    # المشتركون
    "subscriber.created":   N_("إنشاء مشترك"),
    "subscriber.updated":   N_("تحديث مشترك"),
    "subscriber.deleted":   N_("حذف مشترك"),
    "subscriber.activated": N_("تفعيل مشترك"),
    "subscriber.disabled":  N_("تعطيل مشترك"),
    "subscriber.renewed":   N_("تجديد اشتراك"),
    "subscriber.expired":   N_("انتهاء اشتراك"),
    "subscriber.renewal.previewed": N_("معاينة تجديد المشترك"),
    # البطاقات — دورة الحياة
    "card.created":       N_("إنشاء بطاقة"),
    "card.sold":          N_("بيع بطاقة"),
    "card.revoked":       N_("سحب بطاقة"),
    "card.used":          N_("استخدام بطاقة"),
    "card.batch_created": N_("إنشاء حزمة بطاقات"),
    "card.password_reveal": N_("كشف كلمة مرور البطاقة"),
    "card.enable":        N_("تفعيل بطاقة"),
    "card.disable":       N_("تعطيل بطاقة"),
    "card.lock_mac":      N_("قفل عنوان الجهاز"),
    "card.unlock_mac":    N_("فكّ قفل عنوان الجهاز"),
    "card.reset_usage":   N_("تصفير الاستخدام"),
    "card.set_speed":     N_("ضبط سرعة البطاقة"),
    "card.adjust_time":   N_("تعديل الوقت المتبقّي"),
    "card.disconnect":    N_("قطع جلسة البطاقة"),
    "card.soft_delete":   N_("حذف بطاقة"),
    "card.delete_permanent": N_("حذف نهائي للبطاقة"),
    # السلف والدفعات
    "loan.created":    N_("منح سلفة"),
    "loan.settled":    N_("تسوية سلفة"),
    "payment.received": N_("استلام دفعة"),
    "payment.voided":   N_("إلغاء دفعة"),
    # مستخدمو البطاقات
    "card_user.created":          N_("إنشاء مستخدم بطاقة"),
    "card_user.self_registered":  N_("تسجيل ذاتي لمستخدم بطاقة"),
    "card_user.password_updated": N_("تحديث كلمة مرور مستخدم بطاقة"),
    "card_user.card_purchased":   N_("شراء بطاقة"),
    # البطاقات والتسعير — دفعات
    "card_batch.costed":  N_("تسعير دفعة بطاقات"),
    "card_batch.import":  N_("استيراد دفعة بطاقات"),
    "card_batch.restore": N_("استعادة دفعة محذوفة"),
    "batch_generate":     N_("توليد دفعة بطاقات"),
    "batch_archive":      N_("أرشفة دفعة بطاقات"),
    "batch_restore":      N_("استعادة دفعة بطاقات"),
    "price_snapshot.captured": N_("التقاط لقطة سعر"),
    # قوالب طباعة البطاقات
    "card_print_template.create":      N_("إنشاء قالب طباعة بطاقات"),
    "card_print_template.update":      N_("تعديل قالب طباعة بطاقات"),
    "card_print_template.delete":      N_("حذف قالب طباعة بطاقات"),
    "card_print_template.set_default": N_("تعيين قالب الطباعة الافتراضي"),
    "card_print_template.export_pdf":  N_("تصدير قالب PDF"),
    # بوابة كروت الهوتسبوت
    "hotspot_cards_portal.login":       N_("دخول بوابة كروت الهوتسبوت"),
    "hotspot_cards_portal.purchase":    N_("شراء عبر بوابة كروت الهوتسبوت"),
    "hotspot_cards_portal.sms_failed":  N_("فشل إرسال SMS لبوابة الكروت"),
    # الإشعارات وبوابة العميل
    "notification.manual_queued":       N_("جدولة إشعار يدوي"),
    "customer_portal.request_created":  N_("إنشاء طلب من بوابة العميل"),
    # الأمان
    "login.failed":       N_("محاولة دخول فاشلة"),
    "login.success":      N_("تسجيل دخول ناجح"),
    "auth_login":         N_("تسجيل دخول"),
    "auth_login_failed":  N_("محاولة دخول فاشلة"),
    # السرعة المؤقتة
    "temporary_speed.apply":  N_("تطبيق سرعة مؤقتة"),
    "temporary_speed.revert": N_("إرجاع السرعة المؤقتة"),
    "speed_control.dry_run_saved": N_("حفظ تجربة تحكّم السرعة"),
    # RADIUS
    "radius.apply": N_("تطبيق سياسة RADIUS"),
    # المدراء والأدوار
    "role_permissions":  N_("تعديل صلاحيات الدور"),
    "settings_update":   N_("تحديث إعدادات النظام"),
    # التحصيل المالي
    "payment_collection.settings_saved":   N_("حفظ إعدادات التحصيل"),
    "payment_collection.request_approved": N_("اعتماد طلب دفع"),
    "payment_collection.request_rejected": N_("رفض طلب دفع"),
    # الأحداث الدفترية (ledger)
    "ledger.payment":        N_("قيد دفعة"),
    "ledger.renewal":        N_("قيد تجديد"),
    "ledger.debt":           N_("قيد دين"),
    "ledger.loan":           N_("قيد سلفة"),
    "ledger.discount":       N_("قيد خصم"),
    "ledger.wallet_recharge": N_("قيد شحن محفظة"),
    "ledger.card_sale":      N_("قيد بيع بطاقة"),
    "ledger.batch_creation": N_("قيد إنشاء دفعة"),
    "ledger.profit_share":   N_("قيد توزيع أرباح"),
    "ledger.reversal":       N_("قيد عكس"),
    "ledger.correction":     N_("قيد تصحيح"),
    "ledger.void":           N_("إلغاء قيد"),
    "ledger.time_extension": N_("قيد تمديد وقت"),
    "ledger.settlement":     N_("قيد تسوية سلفة"),
    "ledger.debt_settlement": N_("قيد تسديد دين"),
    "ledger.writeoff":       N_("قيد إعفاء من سلفة"),
    "ledger.cash_balance":   N_("قيد رصيد نقديّ"),
    "ledger.quota_topup":    N_("قيد شحن كوتا"),
    "ledger.on_account_credit": N_("قيد رصيد مقدَّم"),
    # الجسر الإداري
    "license.snapshot_refreshed":      N_("تحديث حالة الترخيص"),
    "capacity.contract_refreshed":     N_("تحديث عقد السعة"),
    "usage.report_sent":               N_("إرسال تقرير الاستخدام"),
    "heartbeat.sent":                  N_("إرسال نبض الحالة"),
    "backup.upload_succeeded":         N_("رفع النسخة الاحتياطية"),
    "backup.upload_failed":            N_("تعذر رفع النسخة الاحتياطية"),
    "restore.request_received":        N_("استلام طلب استعادة"),
    "restore.status_changed":          N_("تغيّر حالة الاستعادة"),
    "service_activation.received":     N_("استلام تفعيل خدمة"),
    "service_activation.executed":     N_("تنفيذ تفعيل خدمة"),
    "service_activation.failed":       N_("فشل تفعيل خدمة"),
    "accounting.degraded":             N_("تدهور مسار المحاسبة"),
    # عمليات عامة
    "create":          N_("إنشاء"),
    "update":          N_("تعديل"),
    "delete":          N_("حذف"),
    "disable":         N_("تعطيل"),
    "enable":          N_("تفعيل"),
    "extend_time":     N_("تمديد الوقت"),
    "reset_password":  N_("إعادة تعيين كلمة المرور"),
    "bulk_set_speeds": N_("تحديث جماعي للسرعات"),
    "change_plan":     N_("تغيير باقة المشترك"),
    "revoke":          N_("سحب البطاقة"),
    # ───── خدمات المنافذ (port-script-services) ─────
    # الصيغة الفعلية: mt.port_services.{slug}.{verb}
    "mt.port_services.loop_detect.apply":       N_("تطبيق قاعدة كشف اللوب"),
    "mt.port_services.loop_detect.remove":      N_("إزالة قاعدة كشف اللوب"),
    "mt.port_services.loop_detect.loop_check":  N_("فحص كشف اللوب"),
    "mt.port_services.bt_wifi_block.apply":     N_("تطبيق حجب WiFi عبر TTL"),
    "mt.port_services.bt_wifi_block.remove":    N_("إزالة حجب WiFi عبر TTL"),
    # صيغ بنقاط (تُكتب أحياناً بشرطة)
    "mt.port_services.loop-detect.apply":       N_("تطبيق قاعدة كشف اللوب"),
    "mt.port_services.loop-detect.remove":      N_("إزالة قاعدة كشف اللوب"),
    "mt.port_services.loop-detect.loop_check":  N_("فحص كشف اللوب"),
    "mt.port_services.bt-wifi-block.apply":     N_("تطبيق حجب WiFi عبر TTL"),
    "mt.port_services.bt-wifi-block.remove":    N_("إزالة حجب WiFi عبر TTL"),
    # خدمات المنافذ — مؤشر الفحص الدوري
    "mt.port_services.loop_detect.apply_port":  N_("تطبيق قاعدة لوب (منفذ)"),
    "mt.port_services.loop_detect.remove_port": N_("إزالة قاعدة لوب (منفذ)"),
    # ───── جدولة عرض النطاق الترددي ─────
    "bandwidth_schedule.create":       N_("إنشاء جدولة النطاق الترددي"),
    "bandwidth_schedule.update":       N_("تحديث جدولة النطاق الترددي"),
    "bandwidth_schedule.delete":       N_("حذف جدولة النطاق الترددي"),
    "bandwidth_schedule.enable":       N_("تفعيل جدولة النطاق الترددي"),
    "bandwidth_schedule.disable":      N_("تعطيل جدولة النطاق الترددي"),
    "bandwidth_schedule.bulk_enable":  N_("تفعيل جماعي لجداول النطاق"),
    "bandwidth_schedule.bulk_disable": N_("تعطيل جماعي لجداول النطاق"),
    "bandwidth_schedule.apply_planned": N_("تطبيق جدولة النطاق (مجدوَلة)"),
    "bandwidth_schedule.apply_live":   N_("تطبيق فوري للنطاق الترددي"),
    # ───── النسخ الاحتياطية المحلية ─────
    "backup.local_run":            N_("تشغيل نسخة احتياطية محلية"),
    "backup.local_pruned":         N_("تنظيف نسخ احتياطية قديمة"),
    "backup.local_count_pruned":   N_("تنظيف نسخ احتياطية (بالعدد)"),
    "backup.local_deleted":        N_("حذف نسخة احتياطية"),
    "backup.uploaded_import":      N_("استيراد نسخة احتياطية مرفوعة"),
    "backup.restore_aborted":      N_("إلغاء عملية الاستعادة"),
    "backup.restore_failed":       N_("فشل عملية الاستعادة"),
    "backup.restore_applied":      N_("تطبيق استعادة النسخة الاحتياطية"),
    # ───── قوالب طباعة البطاقات (تسمية موحّدة بنقاط) ─────
    "card_print_template.purge_fixtures": N_("حذف قوالب التهيئة"),
    # ───── أحداث تسجيل الدخول/الخروج ─────
    "auth_login.save":       N_("تسجيل دخول (حفظ الجلسة)"),
    "login.save":            N_("تسجيل دخول (حفظ الجلسة)"),
    # ───── الموزّعون ─────
    "distributor.create":              N_("إنشاء موزّع"),
    "distributor.update":              N_("تحديث موزّع"),
    "distributor.ledger_post":         N_("قيد موزّع"),
    "card_batch.assign_distributor":   N_("تعيين موزّع لدفعة بطاقات"),
    # ───── الأجهزة والشبكة ─────
    "network.device.added":    N_("إضافة جهاز شبكة"),
    "network.device.updated":  N_("تحديث جهاز شبكة"),
    "network.device.deleted":  N_("حذف جهاز شبكة"),
    "network.device.health.check": N_("فحص صحة جهاز الشبكة"),
    "network.policy.created":  N_("إنشاء سياسة شبكة"),
    "network.policy.updated":  N_("تعديل سياسة شبكة"),
    "network.policy.deleted":  N_("حذف سياسة شبكة"),
    # ───── سياسات RADIUS ─────
    "radius.policy.created":   N_("إنشاء سياسة RADIUS"),
    "radius.policy.updated":   N_("تعديل سياسة RADIUS"),
    "radius.policy.deleted":   N_("حذف سياسة RADIUS"),
    # ───── المشتركون (أفعال المدير) ─────
    "subscriber.plan.changed":       N_("تغيير باقة مشترك"),
    "subscriber.speed.temporary":    N_("سرعة مؤقتة للمشترك"),
    "subscriber.speed.reset":        N_("إعادة ضبط سرعة المشترك"),
    "subscriber.time.extended":      N_("تمديد وقت المشترك"),
    "subscriber.free_days.granted":  N_("منح أيام مجانية"),
    "subscriber.trial.started":      N_("بدء فترة تجربة"),
    "subscriber.password.reset":     N_("إعادة تعيين كلمة مرور المشترك"),
    # ───── دُفعات البطاقات (أفعال المدير) ─────
    "card.batch.generated": N_("توليد دفعة بطاقات"),
    "card.batch.archived":  N_("أرشفة دفعة بطاقات"),
    "card.batch.restored":  N_("استعادة دفعة بطاقات"),
    # ───── تذاكر الدعم في المتجر ─────
    "store.support.ticket.opened": N_("فتح تذكرة دعم"),
    "store.support.ticket.closed": N_("إغلاق تذكرة دعم"),
    # ───── الباقات ─────
    "plan.created": N_("إنشاء باقة"),
    "plan.updated": N_("تحديث باقة"),
    "plan.deleted": N_("حذف باقة"),
    # ───── الإعدادات والأدوار ─────
    "settings.updated":           N_("تحديث الإعدادات"),
    "settings_update":            N_("تحديث إعدادات النظام"),
    "system_settings_update":     N_("تحديث إعدادات النظام"),
    "role.permissions.updated":   N_("تحديث صلاحيات الدور"),
    "store_key_rotate":           N_("تدوير مفتاح المتجر"),
    # ───── قوالب الهوتسبوت ─────
    "hotspot.template.created":  N_("إنشاء قالب هوتسبوت"),
    "hotspot.template.updated":  N_("تعديل قالب هوتسبوت"),
    "hotspot.template.deleted":  N_("حذف قالب هوتسبوت"),
    "hotspot.error_messages.save":  N_("حفظ رسائل خطأ الهوتسبوت"),
    "hotspot.error_messages.reset": N_("إعادة تعيين رسائل خطأ الهوتسبوت"),
    # ───── مصمّم صفحة الدخول لـMikroTik ─────
    "mt.login_designer.save":         N_("حفظ تصميم صفحة الدخول"),
    "mt.login_designer.deploy":       N_("نشر تصميم صفحة الدخول"),
    "mt.login_designer.preset_save":  N_("حفظ قالب صفحة دخول"),
    "mt.login_designer.preset_apply": N_("تطبيق قالب صفحة دخول"),
    "mt.login_designer.custom_upload": N_("رفع ملف مخصّص لصفحة الدخول"),
    "mt.login_designer.custom_delete": N_("حذف ملف مخصّص لصفحة الدخول"),
    # ───── نسخ MikroTik الاحتياطية ─────
    "mt.backup.save": N_("حفظ نسخة MikroTik الاحتياطية"),
    # ───── طلبات الخدمة ─────
    "mt.service_request.create": N_("إنشاء طلب خدمة MikroTik"),
    "service_request.create":    N_("إنشاء طلب خدمة"),
    # ───── مجموعات المشاركة ─────
    "add_member":  N_("إضافة عضو لمجموعة"),
    # ───── الجسر الإداري ─────
    "bridge_activated":                        N_("تفعيل الجسر الإداري"),
    "license_admin_bridge_config_update":      N_("تحديث إعداد جسر الترخيص"),
    "license_service_activation_requested":    N_("طلب تفعيل خدمة الترخيص"),
    # ───── NAT ─────
    "nat.rule.add":    N_("إضافة قاعدة NAT"),
    "nat.rule.remove": N_("حذف قاعدة NAT"),
    # ───── خروج الموقع ─────
    "site_exit.apply_attempted":  N_("محاولة تطبيق خروج الموقع"),
    "site_exit.apply_succeeded":  N_("تطبيق خروج الموقع بنجاح"),
    "site_exit.apply_failed":     N_("فشل تطبيق خروج الموقع"),
    # ───── مخزون الشركة ─────
    "company_inventory.item.create":     N_("إضافة صنف للمخزون"),
    "company_inventory.item.deactivate": N_("تعطيل صنف المخزون"),
    "company_inventory.incoming.add":    N_("إضافة وارد للمخزون"),
    "company_inventory.usage.add":       N_("تسجيل استهلاك من المخزون"),
    "company_expense.add":               N_("إضافة مصروف للشركة"),
}

# ── 2) مفردات المُركِّب التلقائي للمفاتيح غير المعرّفة ──
_KEY_PREFIX_NOUNS: dict[str, str] = {
    "store":                N_("المتجر"),
    "store_chat":           N_("شات المتجر"),
    "wallet":               N_("المحفظة"),
    "business_os":          N_("نظام الأعمال"),
    "speed_control":        N_("تحكّم السرعة"),
    "card_user":            N_("مستخدم البطاقة"),
    "card_batch":           N_("دفعة البطاقات"),
    "card_print_template":  N_("قالب طباعة البطاقات"),
    "card":                 N_("البطاقة"),
    "price_snapshot":       N_("لقطة السعر"),
    "hotspot_cards_portal": N_("بوابة كروت الهوتسبوت"),
    "hotspot":              N_("الهوتسبوت"),
    "notification":         N_("الإشعار"),
    "customer_portal":      N_("بوابة العميل"),
    "subscriber":           N_("المشترك"),
    "session":              N_("الجلسة"),
    "backup":               N_("النسخة الاحتياطية"),
    "restore":              N_("الاستعادة"),
    "distributor":          N_("الموزّع"),
    "admin":                N_("المدير"),
    "manager":              N_("المدير"),
    "device":               N_("الجهاز"),
    "nas":                  N_("الراوتر"),
    "plan":                 N_("الباقة"),
    "role":                 N_("الدور"),
    "login":                N_("الدخول"),
    "ledger":               N_("القيد المالي"),
    "payment":              N_("الدفعة"),
    "loan":                 N_("السلفة"),
    "batch":                N_("دفعة البطاقات"),
    "system":               N_("النظام"),
    "license":              N_("الترخيص"),
    "capacity":             N_("السعة"),
    "usage":                N_("الاستخدام"),
    "heartbeat":            N_("نبض الحالة"),
    "accounting":           N_("المحاسبة"),
    "service_activation":   N_("تفعيل الخدمة"),
    "temporary_speed":      N_("السرعة المؤقتة"),
    "radius":               "RADIUS",
    "payment_collection":   N_("التحصيل المالي"),
    # مفاهيم إضافية لأحداث المدراء
    "net":                  N_("الشبكة"),
    "network":              N_("الشبكة"),
    "bandwidth":            N_("عرض النطاق"),
    "bandwidth_schedule":   N_("جدولة النطاق"),
    "nat":                  "NAT",
    "audit":                N_("التدقيق"),
    "mt":                   "MikroTik",
    "settings":             N_("الإعدادات"),
    "service":              N_("الخدمة"),
    "service_request":      N_("طلب الخدمة"),
    "company":              N_("الشركة"),
    "company_inventory":    N_("مخزون الشركة"),
    "company_expense":      N_("مصروف الشركة"),
    "site":                 N_("الموقع"),
    "site_exit":            N_("خروج الموقع"),
    "bridge":               N_("الجسر"),
    "comms":                N_("الاتصالات"),
    "print":                N_("الطباعة"),
    "template":             N_("القالب"),
    "share":                N_("مجموعة المشاركة"),
    "share_groups":         N_("مجموعات المشاركة"),
}

_KEY_VERBS: dict[str, str] = {
    "created":        N_("إنشاء"),
    "create":         N_("إنشاء"),
    "new":            N_("إنشاء"),
    "updated":        N_("تحديث"),
    "update":         N_("تحديث"),
    "edited":         N_("تعديل"),
    "confirmed":      N_("تأكيد"),
    "approved":       N_("اعتماد"),
    "rejected":       N_("رفض"),
    "declined":       N_("رفض"),
    "requested":      N_("طلب"),
    "request":        N_("طلب"),
    "credit":         N_("إضافة"),
    "credited":       N_("إضافة"),
    "debit":          N_("خصم"),
    "debited":        N_("خصم"),
    "saved":          N_("حفظ"),
    "deleted":        N_("حذف"),
    "removed":        N_("حذف"),
    "previewed":      N_("معاينة"),
    "captured":       N_("التقاط"),
    "queued":         N_("جدولة"),
    "scheduled":      N_("جدولة"),
    "failed":         N_("فشل"),
    "success":        N_("نجاح"),
    "succeeded":      N_("نجاح"),
    "login":          N_("دخول"),
    "logout":         N_("خروج"),
    "purchase":       N_("شراء"),
    "purchased":      N_("شراء"),
    "registered":     N_("تسجيل"),
    "self_registered": N_("تسجيل ذاتي"),
    "message":        N_("رسالة"),
    "costed":         N_("تسعير"),
    "applied":        N_("تطبيق"),
    "apply":          N_("تطبيق"),
    "enabled":        N_("تفعيل"),
    "disabled":       N_("تعطيل"),
    "sent":           N_("إرسال"),
    "revert":         N_("تراجع"),
    "reverted":       N_("تراجع"),
    "received":       N_("استلام"),
    "executed":       N_("تنفيذ"),
    "settled":        N_("تسوية"),
    "voided":         N_("إلغاء"),
    "expired":        N_("انتهاء"),
    "renewed":        N_("تجديد"),
    "activated":      N_("تفعيل"),
    "frozen":         N_("تجميد"),
    "unfrozen":       N_("إلغاء تجميد"),
    "closed":         N_("إغلاق"),
    "degraded":       N_("تدهور"),
    "refreshed":      N_("تحديث"),
    "changed":        N_("تغيير"),
    "check":          N_("فحص"),
    "run":            N_("تشغيل"),
    "restore":        N_("استعادة"),
    "reverted":       N_("تراجع"),
    "deployed":       N_("نشر"),
    "deploy":         N_("نشر"),
    "uploaded":       N_("رفع"),
    "upload":         N_("رفع"),
    "download":       N_("تنزيل"),
    "import":         N_("استيراد"),
    "export":         N_("تصدير"),
    "rotate":         N_("تدوير"),
    "assign":         N_("تعيين"),
    "added":          N_("إضافة"),
    "add":            N_("إضافة"),
    "granted":        N_("منح"),
    "grant":          N_("منح"),
    "pruned":         N_("تنظيف"),
    "purge":          N_("حذف نهائي"),
    "aborted":        N_("إلغاء"),
    "abort":          N_("إلغاء"),
    "poll":           N_("استطلاع"),
    "live":           N_("مباشر"),
    "planned":        N_("مجدوَل"),
    "bulk":           N_("جماعي"),
    "save":           N_("حفظ"),
    "reset":          N_("إعادة ضبط"),
    "reveal":         N_("كشف"),
    "lock":           N_("قفل"),
    "unlock":         N_("فكّ قفل"),
    "disconnect":     N_("قطع الاتصال"),
    "soft":           N_("حذف مؤقت"),
    "permanent":      N_("نهائي"),
    "post":           N_("قيد"),
    "costed":         N_("تسعير"),
    "extended":       N_("تمديد"),
    "temporary":      N_("مؤقت"),
}

# ── خريطة أنواع الأهداف (target_type → عربي) ──
TARGET_TYPE_LABELS: dict[str, str] = {
    "card_user":                      N_("مستخدم بطاقة"),
    "speed_control_policy":           N_("سياسة تحكّم السرعة"),
    "subscriber":                     N_("مشترك"),
    "user":                           N_("مشترك"),
    "card":                           N_("بطاقة"),
    "card_batch":                     N_("دفعة بطاقات"),
    "card_print_template":            N_("قالب طباعة بطاقات"),
    "hotspot_card_purchase":          N_("شراء كرت هوتسبوت"),
    "plan":                           N_("باقة"),
    "wallet":                         N_("محفظة"),
    "price_snapshot":                 N_("لقطة سعر"),
    "distributor":                    N_("موزّع"),
    "manager":                        N_("مدير"),
    "admin":                          N_("مدير"),
    "role":                           N_("دور"),
    "tenant":                         N_("مستأجر"),
    "router":                         N_("راوتر"),
    "nas":                            N_("راوتر"),
    "nas_device":                     N_("جهاز راوتر"),
    "device":                         N_("جهاز"),
    "session":                        N_("جلسة"),
    "backup_job":                     N_("مهمة نسخ احتياطي"),
    "backup_file":                    N_("ملف نسخة احتياطية"),
    "backup_retention":               N_("سياسة احتفاظ النسخ"),
    "bandwidth_schedule":             N_("جدولة عرض النطاق"),
    "company_inventory_item":         N_("صنف مخزون الشركة"),
    "company_expense":                N_("مصروف الشركة"),
    "notification_campaign":          N_("حملة إشعارات"),
    "subscriber_group":               N_("مجموعة مشتركين"),
    "ledger":                         N_("قيد مالي"),
    "ledger_entry":                   N_("قيد مالي"),
    "loan":                           N_("سلفة"),
    "payment":                        N_("دفعة"),
    "ticket":                         N_("تذكرة"),
    "system":                         N_("النظام"),
    "setup_wizard_fleet":             N_("أسطول معالج الإعداد"),
    "router_provisioning_registry":   N_("سجل تجهيز الراوترات"),
    "wizard_clients_conf":            N_("إعداد عملاء المعالج"),
    # بيانات قديمة بمسافة بدل شرطة سفلية
    "card batch":                     N_("دفعة بطاقات"),
    # سياسات الوصول والشبكة
    "access_control":                 N_("ضبط الوصول"),
    "allow_mode_policy":              N_("سياسة وضع السماح"),
    "allow_mode_device":              N_("جهاز وضع السماح"),
    "site_exit_policy":               N_("سياسة الخروج"),
    "mac_clone_binding":              N_("ربط استنساخ العنوان"),
    # الترخيص والجسر
    "license_admin_bridge":           N_("جسر إدارة الترخيص"),
    "license_service":                N_("خدمة الترخيص"),
    # الشبكة والبنية
    "bandwidth_profile":              N_("ملف عرض النطاق"),
    "mikrotik_nas":                   N_("راوتر MikroTik"),
    "network_device_monitor_device":  N_("جهاز مراقبة الشبكة"),
    # الإعدادات والخدمات
    "settings":                       N_("إعدادات"),
    "system_settings":                N_("إعدادات النظام"),
    "service_request":                N_("طلب خدمة"),
    "share_group":                    N_("مجموعة مشاركة"),
    "db_retention":                   N_("الاحتفاظ بقاعدة البيانات"),
    "wallet":                         N_("محفظة"),
}

# ── خريطة الفئات (category → عربي) ──
CATEGORY_LABELS: dict[str, str] = {
    "financial":       N_("مالية"),
    "system":          N_("النظام"),
    "card":            N_("البطاقات"),
    "security":        N_("الأمان"),
    "subscriber":      N_("المشتركون"),
    "notification":    N_("الإشعارات"),
    "radius":          "RADIUS",
    "service_request": N_("طلبات الخدمة"),
    "manager":         N_("المدراء"),
    "unknown":         N_("غير مصنّفة"),
}

# ── خريطة مستوى الخطورة (severity → عربي) ──
SEVERITY_LABELS: dict[str, str] = {
    "info":     N_("معلومة"),
    "warning":  N_("تحذير"),
    "critical": N_("حرِج"),
    "error":    N_("خطأ"),
    "debug":    N_("تشخيص"),
}

# ── خريطة نوع الفاعل (actor_type → عربي) ──
ACTOR_TYPE_LABELS: dict[str, str] = {
    "admin":        N_("مدير"),
    "manager":      N_("مدير"),
    "subscriber":   N_("مشترك"),
    "user":         N_("مشترك"),
    "card_user":    N_("مستخدم بطاقة"),
    "distributor":  N_("موزّع"),
    "system":       N_("النظام"),
    "api_token":    N_("واجهة برمجية"),
    "api":          N_("واجهة برمجية"),
    "risk_engine":  N_("محرّك المخاطر"),
    "operator":     N_("مشغّل"),
    "anonymous":    N_("غير معروف"),
}

# ── قواميس الأسماء للمُركِّب (fallback) ──
_FALLBACK_PREFIX_NOUNS: dict[str, str] = {
    "wallet":    N_("محفظة"),
    "store":     N_("متجر"),
    "subscriber": N_("مشترك"),
    "card":      N_("بطاقة"),
    "loan":      N_("سلفة"),
    "payment":   N_("دفعة"),
    "batch":     N_("دفعة بطاقات"),
    "system":    N_("النظام"),
}

_FALLBACK_ACTION_VERBS: dict[str, str] = {
    "created":    N_("إنشاء"),
    "create":     N_("إنشاء"),
    "updated":    N_("تحديث"),
    "update":     N_("تحديث"),
    "deleted":    N_("حذف"),
    "delete":     N_("حذف"),
    "activated":  N_("تفعيل"),
    "activate":   N_("تفعيل"),
    "disabled":   N_("تعطيل"),
    "disable":    N_("تعطيل"),
    "confirmed":  N_("تأكيد"),
    "confirm":    N_("تأكيد"),
    "rejected":   N_("رفض"),
    "reject":     N_("رفض"),
    "requested":  N_("طلب"),
    "request":    N_("طلب"),
    "sold":       N_("بيع"),
    "sell":       N_("بيع"),
    "revoked":    N_("سحب"),
    "revoke":     N_("سحب"),
    "credit":     N_("إضافة"),
    "debit":      N_("خصم"),
}


def _humanize(raw: str) -> str:
    """تأنيس أخير: «a.b_c» → «b c» (آخر مقطع، شُرَط مكان «_» والشرطة).
    لا تُعيد نصًّا يحوي نقاطًا أو أحرفًا لاتينية خامة إذا أمكن التحويل."""
    tail = raw.split(".")[-1] if raw else raw
    out = tail.replace("_", " ").replace("-", " ").strip() or raw
    # لا نُعيد كلمةً لاتينيّةً خامّة للمستخدم («offline»/«limit»): نصٌّ عربيٌّ عامّ
    # والمفتاحُ الخامُّ يبقى في title عند العرض.
    if out and all(ord(ch) < 128 for ch in out):
        return N_("حدث نظام")
    return out


def event_key_label(key: str | None) -> str:
    """مفتاح الحدث بالعربية: خريطة دقيقة ← مُركِّب ← تأنيس. لا تُعيد فراغًا."""
    raw = (key or "").strip()
    if not raw:
        return N_("حدث")
    # 1) خريطة دقيقة
    if raw in EVENT_KEY_LABELS:
        return EVENT_KEY_LABELS[raw]
    # 2) مُركِّب: فِعل + اسم القسم
    parts = raw.split(".")
    prefix = parts[0]
    noun = _KEY_PREFIX_NOUNS.get(prefix)
    last_tokens = parts[-1].split("_") if len(parts) > 1 else []
    verb = next((_KEY_VERBS[t] for t in last_tokens if t in _KEY_VERBS), None)
    if not verb and parts[-1] in _KEY_VERBS:
        verb = _KEY_VERBS[parts[-1]]
    if verb and noun:
        return f"{verb} {noun}"
    if noun:
        return noun
    if verb:
        return verb
    # 3) fallback بالتجزئة: prefix_noun + action_verb
    fallback_noun = _FALLBACK_PREFIX_NOUNS.get(prefix)
    last_part = parts[-1] if parts else raw
    fallback_verb = _FALLBACK_ACTION_VERBS.get(last_part)
    if fallback_verb and fallback_noun:
        return f"{fallback_verb} {fallback_noun}"
    if fallback_noun:
        return fallback_noun
    if fallback_verb:
        return fallback_verb
    # 4) تأنيس أخير
    return _humanize(raw)


def target_type_label(target_type: str | None) -> str:
    """نوع الهدف بالعربية مع تأنيس احتياطي. لا تُعيد فراغًا."""
    raw = (target_type or "").strip()
    if not raw:
        return "—"
    return TARGET_TYPE_LABELS.get(raw.lower(), _humanize(raw))


def category_label(category: str | None) -> str:
    """الفئة بالعربية مع تأنيس احتياطي."""
    raw = (category or "").strip()
    if not raw:
        return "—"
    return CATEGORY_LABELS.get(raw.lower(), _humanize(raw))


def severity_label(severity: str | None) -> str:
    """مستوى الخطورة بالعربية."""
    raw = (severity or "").strip()
    if not raw:
        return "—"
    return SEVERITY_LABELS.get(raw.lower(), raw)


def actor_type_label(actor_type: str | None) -> str:
    """نوع الفاعل بالعربية مع تأنيس احتياطي."""
    raw = (actor_type or "").strip()
    if not raw:
        return "—"
    return ACTOR_TYPE_LABELS.get(raw.lower(), _humanize(raw))
