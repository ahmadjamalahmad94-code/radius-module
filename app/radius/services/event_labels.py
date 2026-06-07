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
# تُغطّي ما يظهر فعليًا في الجدول + الشائع المنبعث عبر الخدمات.
EVENT_KEY_LABELS: dict[str, str] = {
    # المتجر — إيداع/سحب/شات
    "store.deposit_requested": "طلب إيداع في المتجر",
    "store.deposit_confirmed": "تأكيد إيداع في المتجر",
    "store.deposit_rejected": "رفض إيداع في المتجر",
    "store.withdrawal_requested": "طلب سحب من المتجر",
    "store.withdrawal_confirmed": "تأكيد سحب من المتجر",
    "store.withdrawal_rejected": "رفض سحب من المتجر",
    "store_chat.customer_message": "رسالة عميل في شات المتجر",
    # المحفظة
    "wallet.created": "إنشاء محفظة",
    "wallet.credit": "إضافة للمحفظة",
    "wallet.debit": "خصم من المحفظة",
    "business_os.wallet.credit": "إضافة للمحفظة",
    "business_os.wallet.debit": "خصم من المحفظة",
    # تحكّم السرعة
    "speed_control.dry_run_saved": "حفظ تجربة تحكّم السرعة",
    # مستخدمو البطاقات
    "card_user.created": "إنشاء مستخدم بطاقة",
    "card_user.self_registered": "تسجيل ذاتي لمستخدم بطاقة",
    "card_user.password_updated": "تحديث كلمة مرور مستخدم بطاقة",
    "card_user.card_purchased": "شراء بطاقة",
    # البطاقات والتسعير
    "card_batch.costed": "تسعير دفعة بطاقات",
    "price_snapshot.captured": "التقاط لقطة سعر",
    # بوابة كروت الهوتسبوت
    "hotspot_cards_portal.login": "دخول بوابة كروت الهوتسبوت",
    "hotspot_cards_portal.purchase": "شراء عبر بوابة كروت الهوتسبوت",
    "hotspot_cards_portal.sms_failed": "فشل إرسال SMS لبوابة الكروت",
    # الإشعارات وبوابة العميل والمشترك
    "notification.manual_queued": "جدولة إشعار يدوي",
    "customer_portal.request_created": "إنشاء طلب من بوابة العميل",
    "subscriber.renewal.previewed": "معاينة تجديد المشترك",
    # الأمان
    "login.failed": "محاولة دخول فاشلة",
    "login.success": "تسجيل دخول ناجح",
    # البطاقات — عمليات إدارية
    "card.password_reveal": "كشف كلمة مرور البطاقة",
    "card.enable": "تفعيل بطاقة",
    "card.disable": "تعطيل بطاقة",
    "card.lock_mac": "قفل عنوان الجهاز",
    "card.unlock_mac": "فكّ قفل عنوان الجهاز",
    "card.reset_usage": "تصفير الاستخدام",
    "card.set_speed": "ضبط سرعة البطاقة",
    "card.adjust_time": "تعديل الوقت المتبقّي",
    "card.disconnect": "قطع جلسة البطاقة",
    "card.soft_delete": "حذف بطاقة",
    "card.delete_permanent": "حذف نهائي للبطاقة",
    # السرعة المؤقتة
    "temporary_speed.apply": "تطبيق سرعة مؤقتة",
    "temporary_speed.revert": "إرجاع السرعة المؤقتة",
    # قوالب طباعة البطاقات
    "card_print_template.create": "إنشاء قالب طباعة بطاقات",
    "card_print_template.update": "تعديل قالب طباعة بطاقات",
    "card_print_template.delete": "حذف قالب طباعة بطاقات",
    "card_print_template.set_default": "تعيين قالب الطباعة الافتراضي",
    "card_print_template.export_pdf": "تصدير قالب PDF",
    # دفعات البطاقات
    "batch_generate": "توليد دفعة بطاقات",
    "batch_archive": "أرشفة دفعة بطاقات",
    "batch_restore": "استعادة دفعة بطاقات",
    "card_batch.import": "استيراد دفعة بطاقات",
    "card_batch.restore": "استعادة دفعة محذوفة",
    # عمليات المشتركين
    "create": "إنشاء",
    "update": "تعديل",
    "delete": "حذف",
    "disable": "تعطيل",
    "enable": "تفعيل",
    "extend_time": "تمديد الوقت",
    "reset_password": "إعادة تعيين كلمة المرور",
    "bulk_set_speeds": "تحديث جماعي للسرعات",
    "change_plan": "تغيير باقة المشترك",
    "revoke": "سحب البطاقة",
    # إجراءات API / النظام
    "payment_collection.settings_saved": "حفظ إعدادات التحصيل",
    "payment_collection.request_approved": "اعتماد طلب دفع",
    "payment_collection.request_rejected": "رفض طلب دفع",
    "notification.manual_queued": "إرسال إشعار يدوي",
    # السرعة والجلسة
    "bulk_set_speeds": "تحديث جماعي للسرعات",
    "temporary_speed.apply": "تطبيق سرعة مؤقتة",
    "temporary_speed.revert": "إرجاع السرعة المؤقتة",
    # RADIUS
    "radius.apply": "تطبيق سياسة RADIUS",
    # المدراء والأدوار
    "role_permissions": "تعديل صلاحيات الدور",
    "settings_update": "تحديث إعدادات النظام",
    "auth_login": "تسجيل دخول",
    "auth_login_failed": "محاولة دخول فاشلة",
}

# ── 2) مفردات المُركِّب التلقائي للمفاتيح غير المعرّفة ──
# اسم القسم يؤخذ من المقطع الأول (prefix) قبل أول «.».
_KEY_PREFIX_NOUNS: dict[str, str] = {
    "store": "المتجر",
    "store_chat": "شات المتجر",
    "wallet": "المحفظة",
    "business_os": "نظام الأعمال",
    "speed_control": "تحكّم السرعة",
    "card_user": "مستخدم البطاقة",
    "card_batch": "دفعة البطاقات",
    "card": "البطاقة",
    "price_snapshot": "لقطة السعر",
    "hotspot_cards_portal": "بوابة كروت الهوتسبوت",
    "notification": "الإشعار",
    "customer_portal": "بوابة العميل",
    "subscriber": "المشترك",
    "session": "الجلسة",
    "backup": "النسخة الاحتياطية",
    "distributor": "الموزّع",
    "admin": "المدير",
    "device": "الجهاز",
    "nas": "الراوتر",
    "plan": "العرض",
    "role": "الدور",
    "login": "الدخول",
}
# الفِعل يؤخذ من رموز المقطع الأخير (tail tokens) بعد آخر «.».
_KEY_VERBS: dict[str, str] = {
    "created": "إنشاء", "create": "إنشاء", "new": "إنشاء",
    "updated": "تحديث", "update": "تحديث", "edited": "تعديل",
    "confirmed": "تأكيد", "approved": "اعتماد",
    "rejected": "رفض", "declined": "رفض",
    "requested": "طلب", "request": "طلب",
    "credit": "إضافة", "credited": "إضافة",
    "debit": "خصم", "debited": "خصم",
    "saved": "حفظ", "deleted": "حذف", "removed": "حذف",
    "previewed": "معاينة", "captured": "التقاط",
    "queued": "جدولة", "scheduled": "جدولة",
    "failed": "فشل", "success": "نجاح",
    "login": "دخول", "logout": "خروج",
    "purchase": "شراء", "purchased": "شراء",
    "registered": "تسجيل", "self_registered": "تسجيل ذاتي",
    "message": "رسالة", "costed": "تسعير",
    "applied": "تطبيق", "enabled": "تفعيل", "disabled": "تعطيل",
    "sent": "إرسال", "revert": "تراجع", "reverted": "تراجع",
}

# ── خريطة أنواع الأهداف (target_type → عربي) ──
TARGET_TYPE_LABELS: dict[str, str] = {
    "card_user": "مستخدم بطاقة",
    "speed_control_policy": "سياسة تحكّم السرعة",
    "subscriber": "مشترك",
    "user": "مشترك",
    "card": "بطاقة",
    "card_batch": "دفعة بطاقات",
    "card_print_template": "قالب طباعة بطاقات",
    "hotspot_card_purchase": "شراء كرت هوتسبوت",
    "plan": "عرض",
    "wallet": "محفظة",
    "price_snapshot": "لقطة سعر",
    "distributor": "موزّع",
    "manager": "مدير",
    "admin": "مدير",
    "role": "دور",
    "tenant": "مستأجر",
    "router": "راوتر",
    "nas": "راوتر",
    "nas_device": "جهاز راوتر",
    "device": "جهاز",
    "session": "جلسة",
    "backup_job": "مهمة نسخ احتياطي",
    "backup_file": "ملف نسخة احتياطية",
    "backup_retention": "سياسة احتفاظ النسخ",
    "bandwidth_schedule": "جدولة عرض النطاق",
    "company_inventory_item": "صنف مخزون الشركة",
    "company_expense": "مصروف الشركة",
    "notification_campaign": "حملة إشعارات",
    "subscriber_group": "مجموعة مشتركين",
    "ledger": "قيد مالي",
    "loan": "سلفة",
    "payment": "دفعة",
    "ticket": "تذكرة",
    "system": "النظام",
    "setup_wizard_fleet": "أسطول معالج الإعداد",
    "router_provisioning_registry": "سجل تجهيز الراوترات",
    "wizard_clients_conf": "إعداد عملاء المعالج",
}

# ── خريطة الفئات (category → عربي) ──
CATEGORY_LABELS: dict[str, str] = {
    "financial": "مالية",
    "system": "النظام",
    "card": "البطاقات",
    "security": "الأمان",
    "subscriber": "المشتركون",
    "notification": "الإشعارات",
    "radius": "RADIUS",
    "service_request": "طلبات الخدمة",
    "unknown": "غير مصنّفة",
}

# ── خريطة نوع الفاعل (actor_type → عربي) ──
ACTOR_TYPE_LABELS: dict[str, str] = {
    "admin": "مدير",
    "manager": "مدير",
    "subscriber": "مشترك",
    "user": "مشترك",
    "card_user": "مستخدم بطاقة",
    "distributor": "موزّع",
    "system": "النظام",
    "risk_engine": "محرّك المخاطر",
    "operator": "مشغّل",
    "anonymous": "غير معروف",
}


def _humanize(raw: str) -> str:
    """تأنيس أخير: «a.b_c» → «b c» (آخر مقطع، شُرَط مكان «_»)."""
    tail = raw.split(".")[-1] if raw else raw
    return tail.replace("_", " ").strip() or raw


def event_key_label(key: str | None) -> str:
    """مفتاح الحدث بالعربية: خريطة دقيقة ← مُركِّب ← تأنيس. لا تُعيد فراغًا."""
    raw = (key or "").strip()
    if not raw:
        return "حدث"
    if raw in EVENT_KEY_LABELS:
        return EVENT_KEY_LABELS[raw]
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


def actor_type_label(actor_type: str | None) -> str:
    """نوع الفاعل بالعربية مع تأنيس احتياطي."""
    raw = (actor_type or "").strip()
    if not raw:
        return "—"
    return ACTOR_TYPE_LABELS.get(raw.lower(), _humanize(raw))
