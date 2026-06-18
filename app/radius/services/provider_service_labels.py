"""تسميات عربية لمفاتيح خدمات المزوّد (provider service keys → Arabic).

مفتاح الخدمة الذي يَرسله المزوّد (snake_case إنجليزي) → اسم عربي مقروء
يَظهر للمسؤول في صفحة «حالة منح المزوّد» وفي السايدبار والـtooltips.

الهدف: لا يَرى المسؤول مفاتيح إنجليزية خام في الواجهة. المفتاح الخام يبقى
متاحًا كـsubtitle صغير monospace (مفيد للتطابق مع لوحة المزوّد عند الدعم
الفنّي).

المعجم يَجمع:
  • تصنيفي الداخلي (subscribers/cards/reports/finance/network/…)
  • كتالوج المزوّد الموسَّع (accounting/admins/audit_logs/bandwidth_control/
    card_marketplace/card_users/cards_recharge/customer_portal/…)

طبّقنا أسماء عربية متّسقة مع شريط الـsidebar وصفحات الـadmin القائمة كي
يَكون التطابق ذهنيًّا فوريًّا للمستخدم. أيّ مفتاح غير معروف يَسقط إلى
نسخة إنسانية (humanized): استبدال '_' بمسافة + رفع أول حرف. هذا الأسوأ
الذي يَحدث = نص إنجليزي بمسافات بدل underscores، أفضل من snake_case خام.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────
# قاموس الخدمات (service_key → اسم عربي)
# ─────────────────────────────────────────────────────────────────────
SERVICE_NAMES_AR: dict[str, str] = {
    # ── المشتركون والبطاقات ──
    "subscribers":         "المشتركون",
    "subscriber_groups":   "مجموعات المشتركين",
    "cards":               "البطاقات",
    "card_users":          "مستخدمو البطاقات",
    "card_marketplace":    "سوق البطاقات",
    "cards_recharge":      "بطاقات الشحن المسبق",
    "card_checker":        "فحص البطاقات",
    "vouchers":            "القسائم",
    "hotspot_cards":       "بطاقات الهوتسبوت",

    # ── الباقات والعروض والسرعة ──
    "profiles":            "العروض والباقات",
    "plans":               "العروض والباقات",
    "bandwidth_control":   "التحكّم بالسرعة",
    "bandwidth_schedules": "جدولة السرعات",
    "temp_speed":          "السرعة المؤقتة",

    # ── المالية والمحاسبة ──
    "finance":             "المالية",
    "finance_center":      "المركز المالي",
    "accounting":          "المحاسبة والتحصيل",
    "billing":             "الفواتير والتحصيل",
    "payments":            "الدفعات",
    "invoices":            "الفواتير",
    "ledger":              "دفتر الأستاذ",
    "loans":               "السلف",
    "payment_collection":  "تحصيل المدفوعات",
    "admin_pricing":       "تسعير الإدارة",

    # ── التقارير والتدقيق ──
    "reports":             "التقارير",
    "audit":               "التدقيق",
    "audit_logs":          "سجلّ التدقيق",
    "operational_reports": "التقارير التشغيلية",
    "events":              "الأحداث",

    # ── الشبكة والمايكروتيك ──
    "network":             "الشبكة والمايكروتيك",
    "nas":                 "أجهزة NAS",
    "routers":             "الراوترات",
    "devices":             "أجهزة الشبكة",
    "device_health":       "تتبّع صحة الأجهزة",
    "mt_topology":         "خريطة الشبكة",
    "mt_login_designer":   "مصمّم صفحة الدخول",
    "mt_diagnostics":      "تشخيص المايكروتيك",
    "network_policy":      "سياسات الشبكة",
    "site_exit":           "مخرج الموقع",
    "pools":               "نطاقات العناوين",
    "monitoring":          "المراقبة والصحة",
    "router_alerts":       "تنبيهات الراوترات",
    "router_metrics":      "مقاييس الراوترات",

    # ── الأمان والتحكم بالدخول ──
    "access_control":      "التحكم بالدخول",
    "anti_mac_clone":      "منع استنساخ MAC",
    "security":            "الأمان",

    # ── الإدارة والإعدادات ──
    "admins":              "المدراء والصلاحيات",
    "settings":            "الإعدادات",
    "tenants":             "المستأجرون",
    "backups":             "النسخ الاحتياطية",
    "recycle_bin":         "سلّة المحذوفات",
    "lifecycle":           "دورة الحياة",
    "system":              "النظام",
    "tools":               "الأدوات",
    "tokens":              "مفاتيح API",
    "webhooks":            "Webhooks",
    "share_groups":        "مجموعات المشاركة",
    "business_os":         "أعمال HobeOS",
    "print_templates":     "قوالب الطباعة",
    "hotspot_designs":     "تصاميم الهوتسبوت",

    # ── الاتصالات والإشعارات ──
    "communications":      "الرسائل والتنبيهات",
    "messaging":           "الرسائل",
    "notifications":       "الإشعارات",
    "alerts":              "التنبيهات",
    "admin_alerts":        "تنبيهات الإدارة",
    "telegram":            "تلجرام",
    "whatsapp":            "واتساب",
    "whatsapp_bot":        "بوت واتساب",
    "network_telegram":    "تلجرام الشبكة",

    # ── البوّابات والخدمات الذاتية ──
    "customer_portal":     "بوّابة المشترك",
    "subscriber_portal":   "بوّابة المشترك",
    "customer_portals":    "بوّابات الزبائن",
    "service_requests":    "طلبات الخدمات",
    "tickets":             "تذاكر الدعم",

    # ── المتجر والموزّعون ──
    "store":               "المتجر",
    "store_admin":         "إدارة المتجر",
    "store_support":       "دعم المتجر",
    "distributors":        "الموزّعون",
    "marketplace":         "السوق",

    # ── الجلسات والتشغيل ──
    "sessions":            "الجلسات",
    "online":              "المتّصلون الآن",
    "live_session_control":"التحكّم الحيّ بالجلسات",

    # ── مكوّنات النظام الأخرى ──
    "dashboard":           "لوحة المعلومات",
    "setup_wizard":        "معالج الإعداد",
    "license_admin":       "إدارة الترخيص",
    "admin_bridge":        "جسر الإدارة",
    "internal_auth":       "المصادقة الداخلية",
    "health":              "الصحة",
    "i18n":                "تعدّد اللغات",
}


# ─────────────────────────────────────────────────────────────────────
# تسميات الحالات (status raw → عربي)
# ─────────────────────────────────────────────────────────────────────
SERVICE_STATUS_AR: dict[str, str] = {
    # نشطة
    "active":               "مفعّلة",
    "valid":                "صالحة",
    "ok":                   "سليمة",
    "healthy":              "سليمة",
    "grace":                "ضمن سماحية",
    # موقوفة (هارد-سَوسبَند)
    "disabled":             "موقوفة",
    "suspended":            "معلَّقة",
    "expired":              "منتهية",
    "cancelled":            "ملغاة",
    "revoked":              "مسحوبة",
    "denied":               "ممنوعة",
    "blocked":              "محظورة",
    "inactive":             "غير مفعّلة",
    "not_found":            "غير موجودة",
    "invalid_request":      "طلب غير صالح",
    "fingerprint_denied":   "بصمة مرفوضة",
    # locked_upgrade — مدفوعة-غير-مفعّلة
    "locked_upgrade":       "بانتظار التفعيل / ترقية",
    "requires_activation":  "بانتظار التفعيل",
    "requires_upgrade":     "بحاجة ترقية",
    "upgrade_required":     "بحاجة ترقية",
    "paid_not_active":      "مدفوعة — لم تُفعَّل",
    "paid_locked":          "مدفوعة — مقفلة",
    "pending_activation":   "بانتظار التفعيل",
    "not_purchased":        "لم تُشترَ بعد",
    # أخرى محايدة
    "unknown":              "غير معروفة",
    "stale":                "قديمة",
}


# ─────────────────────────────────────────────────────────────────────
# تسميات حالة الميزة (features.<k> → عربي)
# ─────────────────────────────────────────────────────────────────────
FEATURE_STATE_AR: dict[str, str] = {
    "enabled":         "متاحة",
    "locked":          "مقفلة",
    "hidden":          "مخفية",
    "readonly":        "قراءة فقط",
    "read_only":       "قراءة فقط",
    "locked_upgrade":  "قفل ترقية",
    "requires_activation": "بانتظار التفعيل",
    "upgrade_required": "بحاجة ترقية",
}


# ─────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────
def service_label_ar(service_key: str) -> str:
    """يُرجع الاسم العربي لمفتاح خدمة. يَستعمل خريطة معروفة + fallback إنسانيّ
    (replace '_' with ' ', capitalize) للمفاتيح غير المُعجَمة. لا يُرجع
    سلسلة فارغة أبدًا."""
    if not service_key:
        return ""
    k = str(service_key).strip().lower()
    if not k:
        return ""
    if k in SERVICE_NAMES_AR:
        return SERVICE_NAMES_AR[k]
    # fallback: humanize — أحسن من snake_case خام، لكن يَبقى إنجليزيًّا للمفاتيح
    # غير المسجَّلة. توسعة الـmap لاحقًا تَحلّ.
    return k.replace("_", " ").replace("-", " ").strip().title()


def service_status_ar(status: str) -> str:
    """يُرجع الاسم العربي لحالة خدمة. للحالات غير المعروفة يُعيد القيمة الخام."""
    if not status:
        return ""
    return SERVICE_STATUS_AR.get(str(status).strip().lower(), str(status))


def feature_state_ar(state: str) -> str:
    """يُرجع الاسم العربي لحالة ميزة (features.<k>)."""
    if not state:
        return ""
    return FEATURE_STATE_AR.get(str(state).strip().lower(), str(state))


__all__ = [
    "SERVICE_NAMES_AR", "SERVICE_STATUS_AR", "FEATURE_STATE_AR",
    "service_label_ar", "service_status_ar", "feature_state_ar",
]
