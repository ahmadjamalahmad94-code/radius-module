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

# ── 1) خريطة دقيقة لمفاتيح الأحداث المعروفة (event_key → عربي) ──
EVENT_KEY_LABELS: dict[str, str] = {
    # المحفظة
    "wallet.created":    "إنشاء محفظة",
    "wallet.credit":     "إضافة للمحفظة",
    "wallet.debit":      "خصم من المحفظة",
    "wallet.frozen":     "تجميد المحفظة",
    "wallet.unfrozen":   "إلغاء تجميد المحفظة",
    "wallet.closed":     "إغلاق المحفظة",
    "business_os.wallet.credit": "إضافة للمحفظة",
    "business_os.wallet.debit":  "خصم من المحفظة",
    # المتجر — إيداع / سحب / شات / تسجيل / حزمة
    "store.deposit_requested":   "طلب إيداع",
    "store.deposit_confirmed":   "تأكيد إيداع",
    "store.deposit_rejected":    "رفض إيداع",
    "store.withdrawal_requested": "طلب سحب",
    "store.withdrawal_confirmed": "تأكيد سحب",
    "store.withdrawal_rejected":  "رفض سحب",
    "store.chat.customer_message": "رسالة عميل في المتجر",
    "store.chat.admin_message":    "رد المتجر على العميل",
    "store.registration":          "تسجيل في المتجر",
    "store.package_purchased":     "شراء حزمة من المتجر",
    "store_chat.customer_message": "رسالة عميل في شات المتجر",
    # المشتركون
    "subscriber.created":   "إنشاء مشترك",
    "subscriber.updated":   "تحديث مشترك",
    "subscriber.deleted":   "حذف مشترك",
    "subscriber.activated": "تفعيل مشترك",
    "subscriber.disabled":  "تعطيل مشترك",
    "subscriber.renewed":   "تجديد اشتراك",
    "subscriber.expired":   "انتهاء اشتراك",
    "subscriber.renewal.previewed": "معاينة تجديد المشترك",
    # البطاقات — دورة الحياة
    "card.created":       "إنشاء بطاقة",
    "card.sold":          "بيع بطاقة",
    "card.revoked":       "سحب بطاقة",
    "card.used":          "استخدام بطاقة",
    "card.batch_created": "إنشاء حزمة بطاقات",
    "card.password_reveal": "كشف كلمة مرور البطاقة",
    "card.enable":        "تفعيل بطاقة",
    "card.disable":       "تعطيل بطاقة",
    "card.lock_mac":      "قفل عنوان الجهاز",
    "card.unlock_mac":    "فكّ قفل عنوان الجهاز",
    "card.reset_usage":   "تصفير الاستخدام",
    "card.set_speed":     "ضبط سرعة البطاقة",
    "card.adjust_time":   "تعديل الوقت المتبقّي",
    "card.disconnect":    "قطع جلسة البطاقة",
    "card.soft_delete":   "حذف بطاقة",
    "card.delete_permanent": "حذف نهائي للبطاقة",
    # السلف والدفعات
    "loan.created":    "منح سلفة",
    "loan.settled":    "تسوية سلفة",
    "payment.received": "استلام دفعة",
    "payment.voided":   "إلغاء دفعة",
    # مستخدمو البطاقات
    "card_user.created":          "إنشاء مستخدم بطاقة",
    "card_user.self_registered":  "تسجيل ذاتي لمستخدم بطاقة",
    "card_user.password_updated": "تحديث كلمة مرور مستخدم بطاقة",
    "card_user.card_purchased":   "شراء بطاقة",
    # البطاقات والتسعير — دفعات
    "card_batch.costed":  "تسعير دفعة بطاقات",
    "card_batch.import":  "استيراد دفعة بطاقات",
    "card_batch.restore": "استعادة دفعة محذوفة",
    "batch_generate":     "توليد دفعة بطاقات",
    "batch_archive":      "أرشفة دفعة بطاقات",
    "batch_restore":      "استعادة دفعة بطاقات",
    "price_snapshot.captured": "التقاط لقطة سعر",
    # قوالب طباعة البطاقات
    "card_print_template.create":      "إنشاء قالب طباعة بطاقات",
    "card_print_template.update":      "تعديل قالب طباعة بطاقات",
    "card_print_template.delete":      "حذف قالب طباعة بطاقات",
    "card_print_template.set_default": "تعيين قالب الطباعة الافتراضي",
    "card_print_template.export_pdf":  "تصدير قالب PDF",
    # بوابة كروت الهوتسبوت
    "hotspot_cards_portal.login":       "دخول بوابة كروت الهوتسبوت",
    "hotspot_cards_portal.purchase":    "شراء عبر بوابة كروت الهوتسبوت",
    "hotspot_cards_portal.sms_failed":  "فشل إرسال SMS لبوابة الكروت",
    # الإشعارات وبوابة العميل
    "notification.manual_queued":       "جدولة إشعار يدوي",
    "customer_portal.request_created":  "إنشاء طلب من بوابة العميل",
    # الأمان
    "login.failed":       "محاولة دخول فاشلة",
    "login.success":      "تسجيل دخول ناجح",
    "auth_login":         "تسجيل دخول",
    "auth_login_failed":  "محاولة دخول فاشلة",
    # السرعة المؤقتة
    "temporary_speed.apply":  "تطبيق سرعة مؤقتة",
    "temporary_speed.revert": "إرجاع السرعة المؤقتة",
    "speed_control.dry_run_saved": "حفظ تجربة تحكّم السرعة",
    # RADIUS
    "radius.apply": "تطبيق سياسة RADIUS",
    # المدراء والأدوار
    "role_permissions":  "تعديل صلاحيات الدور",
    "settings_update":   "تحديث إعدادات النظام",
    # التحصيل المالي
    "payment_collection.settings_saved":   "حفظ إعدادات التحصيل",
    "payment_collection.request_approved": "اعتماد طلب دفع",
    "payment_collection.request_rejected": "رفض طلب دفع",
    # الأحداث الدفترية (ledger)
    "ledger.payment":        "قيد دفعة",
    "ledger.renewal":        "قيد تجديد",
    "ledger.debt":           "قيد دين",
    "ledger.loan":           "قيد سلفة",
    "ledger.discount":       "قيد خصم",
    "ledger.wallet_recharge": "قيد شحن محفظة",
    "ledger.card_sale":      "قيد بيع بطاقة",
    "ledger.batch_creation": "قيد إنشاء دفعة",
    "ledger.profit_share":   "قيد توزيع أرباح",
    "ledger.reversal":       "قيد عكس",
    "ledger.correction":     "قيد تصحيح",
    # الجسر الإداري
    "license.snapshot_refreshed":      "تحديث حالة الترخيص",
    "capacity.contract_refreshed":     "تحديث عقد السعة",
    "usage.report_sent":               "إرسال تقرير الاستخدام",
    "heartbeat.sent":                  "إرسال نبض الحالة",
    "backup.upload_succeeded":         "رفع النسخة الاحتياطية",
    "backup.upload_failed":            "تعذر رفع النسخة الاحتياطية",
    "restore.request_received":        "استلام طلب استعادة",
    "restore.status_changed":          "تغيّر حالة الاستعادة",
    "service_activation.received":     "استلام تفعيل خدمة",
    "service_activation.executed":     "تنفيذ تفعيل خدمة",
    "service_activation.failed":       "فشل تفعيل خدمة",
    "accounting.degraded":             "تدهور مسار المحاسبة",
    # عمليات عامة
    "create":          "إنشاء",
    "update":          "تعديل",
    "delete":          "حذف",
    "disable":         "تعطيل",
    "enable":          "تفعيل",
    "extend_time":     "تمديد الوقت",
    "reset_password":  "إعادة تعيين كلمة المرور",
    "bulk_set_speeds": "تحديث جماعي للسرعات",
    "change_plan":     "تغيير باقة المشترك",
    "revoke":          "سحب البطاقة",
    # ───── خدمات المنافذ (port-script-services) ─────
    # الصيغة الفعلية: mt.port_services.{slug}.{verb}
    "mt.port_services.loop_detect.apply":       "تطبيق قاعدة كشف اللوب",
    "mt.port_services.loop_detect.remove":      "إزالة قاعدة كشف اللوب",
    "mt.port_services.loop_detect.loop_check":  "فحص كشف اللوب",
    "mt.port_services.bt_wifi_block.apply":     "تطبيق حجب WiFi عبر TTL",
    "mt.port_services.bt_wifi_block.remove":    "إزالة حجب WiFi عبر TTL",
    # صيغ بنقاط (تُكتب أحياناً بشرطة)
    "mt.port_services.loop-detect.apply":       "تطبيق قاعدة كشف اللوب",
    "mt.port_services.loop-detect.remove":      "إزالة قاعدة كشف اللوب",
    "mt.port_services.loop-detect.loop_check":  "فحص كشف اللوب",
    "mt.port_services.bt-wifi-block.apply":     "تطبيق حجب WiFi عبر TTL",
    "mt.port_services.bt-wifi-block.remove":    "إزالة حجب WiFi عبر TTL",
    # خدمات المنافذ — مؤشر الفحص الدوري
    "mt.port_services.loop_detect.apply_port":  "تطبيق قاعدة لوب (منفذ)",
    "mt.port_services.loop_detect.remove_port": "إزالة قاعدة لوب (منفذ)",
    # ───── جدولة عرض النطاق الترددي ─────
    "bandwidth_schedule.create":       "إنشاء جدولة النطاق الترددي",
    "bandwidth_schedule.update":       "تحديث جدولة النطاق الترددي",
    "bandwidth_schedule.delete":       "حذف جدولة النطاق الترددي",
    "bandwidth_schedule.enable":       "تفعيل جدولة النطاق الترددي",
    "bandwidth_schedule.disable":      "تعطيل جدولة النطاق الترددي",
    "bandwidth_schedule.bulk_enable":  "تفعيل جماعي لجداول النطاق",
    "bandwidth_schedule.bulk_disable": "تعطيل جماعي لجداول النطاق",
    "bandwidth_schedule.apply_planned": "تطبيق جدولة النطاق (مجدوَلة)",
    "bandwidth_schedule.apply_live":   "تطبيق فوري للنطاق الترددي",
    # ───── النسخ الاحتياطية المحلية ─────
    "backup.local_run":            "تشغيل نسخة احتياطية محلية",
    "backup.local_pruned":         "تنظيف نسخ احتياطية قديمة",
    "backup.local_count_pruned":   "تنظيف نسخ احتياطية (بالعدد)",
    "backup.local_deleted":        "حذف نسخة احتياطية",
    "backup.uploaded_import":      "استيراد نسخة احتياطية مرفوعة",
    "backup.restore_aborted":      "إلغاء عملية الاستعادة",
    "backup.restore_failed":       "فشل عملية الاستعادة",
    "backup.restore_applied":      "تطبيق استعادة النسخة الاحتياطية",
    # ───── قوالب طباعة البطاقات (تسمية موحّدة بنقاط) ─────
    "card_print_template.purge_fixtures": "حذف قوالب التهيئة",
    # ───── أحداث تسجيل الدخول/الخروج ─────
    "auth_login.save":       "تسجيل دخول (حفظ الجلسة)",
    "login.save":            "تسجيل دخول (حفظ الجلسة)",
    # ───── الموزّعون ─────
    "distributor.create":              "إنشاء موزّع",
    "distributor.update":              "تحديث موزّع",
    "distributor.ledger_post":         "قيد موزّع",
    "card_batch.assign_distributor":   "تعيين موزّع لدفعة بطاقات",
    # ───── الأجهزة والشبكة ─────
    "network.device.added":    "إضافة جهاز شبكة",
    "network.device.updated":  "تحديث جهاز شبكة",
    "network.device.deleted":  "حذف جهاز شبكة",
    "network.device.health.check": "فحص صحة جهاز الشبكة",
    "network.policy.created":  "إنشاء سياسة شبكة",
    "network.policy.updated":  "تعديل سياسة شبكة",
    "network.policy.deleted":  "حذف سياسة شبكة",
    # ───── سياسات RADIUS ─────
    "radius.policy.created":   "إنشاء سياسة RADIUS",
    "radius.policy.updated":   "تعديل سياسة RADIUS",
    "radius.policy.deleted":   "حذف سياسة RADIUS",
    # ───── المشتركون (أفعال المدير) ─────
    "subscriber.plan.changed":       "تغيير باقة مشترك",
    "subscriber.speed.temporary":    "سرعة مؤقتة للمشترك",
    "subscriber.speed.reset":        "إعادة ضبط سرعة المشترك",
    "subscriber.time.extended":      "تمديد وقت المشترك",
    "subscriber.free_days.granted":  "منح أيام مجانية",
    "subscriber.trial.started":      "بدء فترة تجربة",
    "subscriber.password.reset":     "إعادة تعيين كلمة مرور المشترك",
    # ───── دُفعات البطاقات (أفعال المدير) ─────
    "card.batch.generated": "توليد دفعة بطاقات",
    "card.batch.archived":  "أرشفة دفعة بطاقات",
    "card.batch.restored":  "استعادة دفعة بطاقات",
    # ───── تذاكر الدعم في المتجر ─────
    "store.support.ticket.opened": "فتح تذكرة دعم",
    "store.support.ticket.closed": "إغلاق تذكرة دعم",
    # ───── الباقات ─────
    "plan.created": "إنشاء باقة",
    "plan.updated": "تحديث باقة",
    "plan.deleted": "حذف باقة",
    # ───── الإعدادات والأدوار ─────
    "settings.updated":           "تحديث الإعدادات",
    "settings_update":            "تحديث إعدادات النظام",
    "system_settings_update":     "تحديث إعدادات النظام",
    "role.permissions.updated":   "تحديث صلاحيات الدور",
    "store_key_rotate":           "تدوير مفتاح المتجر",
    # ───── قوالب الهوتسبوت ─────
    "hotspot.template.created":  "إنشاء قالب هوتسبوت",
    "hotspot.template.updated":  "تعديل قالب هوتسبوت",
    "hotspot.template.deleted":  "حذف قالب هوتسبوت",
    "hotspot.error_messages.save":  "حفظ رسائل خطأ الهوتسبوت",
    "hotspot.error_messages.reset": "إعادة تعيين رسائل خطأ الهوتسبوت",
    # ───── مصمّم صفحة الدخول لـMikroTik ─────
    "mt.login_designer.save":         "حفظ تصميم صفحة الدخول",
    "mt.login_designer.deploy":       "نشر تصميم صفحة الدخول",
    "mt.login_designer.preset_save":  "حفظ قالب صفحة دخول",
    "mt.login_designer.preset_apply": "تطبيق قالب صفحة دخول",
    "mt.login_designer.custom_upload": "رفع ملف مخصّص لصفحة الدخول",
    "mt.login_designer.custom_delete": "حذف ملف مخصّص لصفحة الدخول",
    # ───── نسخ MikroTik الاحتياطية ─────
    "mt.backup.save": "حفظ نسخة MikroTik الاحتياطية",
    # ───── طلبات الخدمة ─────
    "mt.service_request.create": "إنشاء طلب خدمة MikroTik",
    "service_request.create":    "إنشاء طلب خدمة",
    # ───── مجموعات المشاركة ─────
    "add_member":  "إضافة عضو لمجموعة",
    # ───── الجسر الإداري ─────
    "bridge_activated":                        "تفعيل الجسر الإداري",
    "license_admin_bridge_config_update":      "تحديث إعداد جسر الترخيص",
    "license_service_activation_requested":    "طلب تفعيل خدمة الترخيص",
    # ───── الاتصالات ─────
    "comms_quota_package_requested": "طلب حزمة اتصالات",
    "comms_quota_manual_credit":     "إضافة يدوية لرصيد الاتصالات",
    # ───── NAT ─────
    "nat.rule.add":    "إضافة قاعدة NAT",
    "nat.rule.remove": "حذف قاعدة NAT",
    # ───── خروج الموقع ─────
    "site_exit.apply_attempted":  "محاولة تطبيق خروج الموقع",
    "site_exit.apply_succeeded":  "تطبيق خروج الموقع بنجاح",
    "site_exit.apply_failed":     "فشل تطبيق خروج الموقع",
    # ───── مخزون الشركة ─────
    "company_inventory.item.create":     "إضافة صنف للمخزون",
    "company_inventory.item.deactivate": "تعطيل صنف المخزون",
    "company_inventory.incoming.add":    "إضافة وارد للمخزون",
    "company_inventory.usage.add":       "تسجيل استهلاك من المخزون",
    "company_expense.add":               "إضافة مصروف للشركة",
}

# ── 2) مفردات المُركِّب التلقائي للمفاتيح غير المعرّفة ──
_KEY_PREFIX_NOUNS: dict[str, str] = {
    "store":                "المتجر",
    "store_chat":           "شات المتجر",
    "wallet":               "المحفظة",
    "business_os":          "نظام الأعمال",
    "speed_control":        "تحكّم السرعة",
    "card_user":            "مستخدم البطاقة",
    "card_batch":           "دفعة البطاقات",
    "card_print_template":  "قالب طباعة البطاقات",
    "card":                 "البطاقة",
    "price_snapshot":       "لقطة السعر",
    "hotspot_cards_portal": "بوابة كروت الهوتسبوت",
    "hotspot":              "الهوتسبوت",
    "notification":         "الإشعار",
    "customer_portal":      "بوابة العميل",
    "subscriber":           "المشترك",
    "session":              "الجلسة",
    "backup":               "النسخة الاحتياطية",
    "restore":              "الاستعادة",
    "distributor":          "الموزّع",
    "admin":                "المدير",
    "manager":              "المدير",
    "device":               "الجهاز",
    "nas":                  "الراوتر",
    "plan":                 "الباقة",
    "role":                 "الدور",
    "login":                "الدخول",
    "ledger":               "القيد المالي",
    "payment":              "الدفعة",
    "loan":                 "السلفة",
    "batch":                "دفعة البطاقات",
    "system":               "النظام",
    "license":              "الترخيص",
    "capacity":             "السعة",
    "usage":                "الاستخدام",
    "heartbeat":            "نبض الحالة",
    "accounting":           "المحاسبة",
    "service_activation":   "تفعيل الخدمة",
    "temporary_speed":      "السرعة المؤقتة",
    "radius":               "RADIUS",
    "payment_collection":   "التحصيل المالي",
    # مفاهيم إضافية لأحداث المدراء
    "net":                  "الشبكة",
    "network":              "الشبكة",
    "bandwidth":            "عرض النطاق",
    "bandwidth_schedule":   "جدولة النطاق",
    "nat":                  "NAT",
    "audit":                "التدقيق",
    "mt":                   "MikroTik",
    "settings":             "الإعدادات",
    "service":              "الخدمة",
    "service_request":      "طلب الخدمة",
    "company":              "الشركة",
    "company_inventory":    "مخزون الشركة",
    "company_expense":      "مصروف الشركة",
    "site":                 "الموقع",
    "site_exit":            "خروج الموقع",
    "bridge":               "الجسر",
    "comms":                "الاتصالات",
    "print":                "الطباعة",
    "template":             "القالب",
    "share":                "مجموعة المشاركة",
    "share_groups":         "مجموعات المشاركة",
}

_KEY_VERBS: dict[str, str] = {
    "created":        "إنشاء",
    "create":         "إنشاء",
    "new":            "إنشاء",
    "updated":        "تحديث",
    "update":         "تحديث",
    "edited":         "تعديل",
    "confirmed":      "تأكيد",
    "approved":       "اعتماد",
    "rejected":       "رفض",
    "declined":       "رفض",
    "requested":      "طلب",
    "request":        "طلب",
    "credit":         "إضافة",
    "credited":       "إضافة",
    "debit":          "خصم",
    "debited":        "خصم",
    "saved":          "حفظ",
    "deleted":        "حذف",
    "removed":        "حذف",
    "previewed":      "معاينة",
    "captured":       "التقاط",
    "queued":         "جدولة",
    "scheduled":      "جدولة",
    "failed":         "فشل",
    "success":        "نجاح",
    "succeeded":      "نجاح",
    "login":          "دخول",
    "logout":         "خروج",
    "purchase":       "شراء",
    "purchased":      "شراء",
    "registered":     "تسجيل",
    "self_registered": "تسجيل ذاتي",
    "message":        "رسالة",
    "costed":         "تسعير",
    "applied":        "تطبيق",
    "apply":          "تطبيق",
    "enabled":        "تفعيل",
    "disabled":       "تعطيل",
    "sent":           "إرسال",
    "revert":         "تراجع",
    "reverted":       "تراجع",
    "received":       "استلام",
    "executed":       "تنفيذ",
    "settled":        "تسوية",
    "voided":         "إلغاء",
    "expired":        "انتهاء",
    "renewed":        "تجديد",
    "activated":      "تفعيل",
    "frozen":         "تجميد",
    "unfrozen":       "إلغاء تجميد",
    "closed":         "إغلاق",
    "degraded":       "تدهور",
    "refreshed":      "تحديث",
    "changed":        "تغيير",
    "check":          "فحص",
    "run":            "تشغيل",
    "restore":        "استعادة",
    "reverted":       "تراجع",
    "deployed":       "نشر",
    "deploy":         "نشر",
    "uploaded":       "رفع",
    "upload":         "رفع",
    "download":       "تنزيل",
    "import":         "استيراد",
    "export":         "تصدير",
    "rotate":         "تدوير",
    "assign":         "تعيين",
    "added":          "إضافة",
    "add":            "إضافة",
    "granted":        "منح",
    "grant":          "منح",
    "pruned":         "تنظيف",
    "purge":          "حذف نهائي",
    "aborted":        "إلغاء",
    "abort":          "إلغاء",
    "poll":           "استطلاع",
    "live":           "مباشر",
    "planned":        "مجدوَل",
    "bulk":           "جماعي",
    "save":           "حفظ",
    "reset":          "إعادة ضبط",
    "reveal":         "كشف",
    "lock":           "قفل",
    "unlock":         "فكّ قفل",
    "disconnect":     "قطع الاتصال",
    "soft":           "حذف مؤقت",
    "permanent":      "نهائي",
    "post":           "قيد",
    "costed":         "تسعير",
    "extended":       "تمديد",
    "temporary":      "مؤقت",
}

# ── خريطة أنواع الأهداف (target_type → عربي) ──
TARGET_TYPE_LABELS: dict[str, str] = {
    "card_user":                      "مستخدم بطاقة",
    "speed_control_policy":           "سياسة تحكّم السرعة",
    "subscriber":                     "مشترك",
    "user":                           "مشترك",
    "card":                           "بطاقة",
    "card_batch":                     "دفعة بطاقات",
    "card_print_template":            "قالب طباعة بطاقات",
    "hotspot_card_purchase":          "شراء كرت هوتسبوت",
    "plan":                           "باقة",
    "wallet":                         "محفظة",
    "price_snapshot":                 "لقطة سعر",
    "distributor":                    "موزّع",
    "manager":                        "مدير",
    "admin":                          "مدير",
    "role":                           "دور",
    "tenant":                         "مستأجر",
    "router":                         "راوتر",
    "nas":                            "راوتر",
    "nas_device":                     "جهاز راوتر",
    "device":                         "جهاز",
    "session":                        "جلسة",
    "backup_job":                     "مهمة نسخ احتياطي",
    "backup_file":                    "ملف نسخة احتياطية",
    "backup_retention":               "سياسة احتفاظ النسخ",
    "bandwidth_schedule":             "جدولة عرض النطاق",
    "company_inventory_item":         "صنف مخزون الشركة",
    "company_expense":                "مصروف الشركة",
    "notification_campaign":          "حملة إشعارات",
    "subscriber_group":               "مجموعة مشتركين",
    "ledger":                         "قيد مالي",
    "loan":                           "سلفة",
    "payment":                        "دفعة",
    "ticket":                         "تذكرة",
    "system":                         "النظام",
    "setup_wizard_fleet":             "أسطول معالج الإعداد",
    "router_provisioning_registry":   "سجل تجهيز الراوترات",
    "wizard_clients_conf":            "إعداد عملاء المعالج",
}

# ── خريطة الفئات (category → عربي) ──
CATEGORY_LABELS: dict[str, str] = {
    "financial":       "مالية",
    "system":          "النظام",
    "card":            "البطاقات",
    "security":        "الأمان",
    "subscriber":      "المشتركون",
    "notification":    "الإشعارات",
    "radius":          "RADIUS",
    "service_request": "طلبات الخدمة",
    "manager":         "المدراء",
    "unknown":         "غير مصنّفة",
}

# ── خريطة مستوى الخطورة (severity → عربي) ──
SEVERITY_LABELS: dict[str, str] = {
    "info":     "معلومة",
    "warning":  "تحذير",
    "critical": "حرِج",
    "error":    "خطأ",
    "debug":    "تشخيص",
}

# ── خريطة نوع الفاعل (actor_type → عربي) ──
ACTOR_TYPE_LABELS: dict[str, str] = {
    "admin":        "مدير",
    "manager":      "مدير",
    "subscriber":   "مشترك",
    "user":         "مشترك",
    "card_user":    "مستخدم بطاقة",
    "distributor":  "موزّع",
    "system":       "النظام",
    "api_token":    "واجهة برمجية",
    "api":          "واجهة برمجية",
    "risk_engine":  "محرّك المخاطر",
    "operator":     "مشغّل",
    "anonymous":    "غير معروف",
}

# ── قواميس الأسماء للمُركِّب (fallback) ──
_FALLBACK_PREFIX_NOUNS: dict[str, str] = {
    "wallet":    "محفظة",
    "store":     "متجر",
    "subscriber": "مشترك",
    "card":      "بطاقة",
    "loan":      "سلفة",
    "payment":   "دفعة",
    "batch":     "دفعة بطاقات",
    "system":    "النظام",
}

_FALLBACK_ACTION_VERBS: dict[str, str] = {
    "created":    "إنشاء",
    "create":     "إنشاء",
    "updated":    "تحديث",
    "update":     "تحديث",
    "deleted":    "حذف",
    "delete":     "حذف",
    "activated":  "تفعيل",
    "activate":   "تفعيل",
    "disabled":   "تعطيل",
    "disable":    "تعطيل",
    "confirmed":  "تأكيد",
    "confirm":    "تأكيد",
    "rejected":   "رفض",
    "reject":     "رفض",
    "requested":  "طلب",
    "request":    "طلب",
    "sold":       "بيع",
    "sell":       "بيع",
    "revoked":    "سحب",
    "revoke":     "سحب",
    "credit":     "إضافة",
    "debit":      "خصم",
}


def _humanize(raw: str) -> str:
    """تأنيس أخير: «a.b_c» → «b c» (آخر مقطع، شُرَط مكان «_» والشرطة).
    لا تُعيد نصًّا يحوي نقاطًا أو أحرفًا لاتينية خامة إذا أمكن التحويل."""
    tail = raw.split(".")[-1] if raw else raw
    return tail.replace("_", " ").replace("-", " ").strip() or raw


def event_key_label(key: str | None) -> str:
    """مفتاح الحدث بالعربية: خريطة دقيقة ← مُركِّب ← تأنيس. لا تُعيد فراغًا."""
    raw = (key or "").strip()
    if not raw:
        return "حدث"
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
