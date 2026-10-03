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
from app.i18n_text import N_

# ─────────────────────────────────────────────────────────────────────
# قاموس الخدمات (service_key → اسم عربي)
# ─────────────────────────────────────────────────────────────────────
SERVICE_NAMES_AR: dict[str, str] = {
    # ── مفاتيح صفحاتٍ سُجِّلت في بوّابة المزوّد لاحقًا (provider_gate) ──
    "approvals":               N_("موافقات المدراء"),
    "credit":                  N_("رصيد المدراء والموزّعين"),
    "data_export":             N_("تصدير البيانات"),
    "data_migration":          N_("ترحيل البيانات"),
    "data_reset":              N_("تصفير البيانات"),
    "demo_cleanup":            N_("تنظيف البيانات التجريبية"),
    "docs":                    N_("مركز الأدلّة"),
    "integrations":            N_("التكاملات"),
    "services_catalog":        N_("دليل الخدمات"),
    "sms":                     N_("الرسائل النصّية"),
    "subscriber_notifications": N_("إشعارات المشتركين"),

    # ── المشتركون والبطاقات ──
    "subscribers":         N_("المشتركون"),
    "subscriber_groups":   N_("مجموعات المشتركين"),
    "cards":               N_("البطاقات"),
    "card_users":          N_("مستخدمو البطاقات"),
    "card_marketplace":    N_("سوق البطاقات"),
    "cards_recharge":      N_("بطاقات الشحن المسبق"),
    "card_checker":        N_("فحص البطاقات"),
    "vouchers":            N_("القسائم"),
    "hotspot_cards":       N_("بطاقات الهوتسبوت"),

    # ── الباقات والعروض والسرعة ──
    "profiles":            N_("العروض والباقات"),
    "plans":               N_("العروض والباقات"),
    "bandwidth_control":   N_("التحكّم بالسرعة"),
    "bandwidth_schedules": N_("جدولة السرعات"),
    "temp_speed":          N_("السرعة المؤقتة"),

    # ── المالية والمحاسبة ──
    "finance":             N_("المالية"),
    "finance_center":      N_("المركز المالي"),
    "accounting":          N_("المحاسبة والتحصيل"),
    "billing":             N_("الفواتير والتحصيل"),
    "payments":            N_("الدفعات"),
    "invoices":            N_("الفواتير"),
    "ledger":              N_("دفتر الأستاذ"),
    "loans":               N_("السلف"),
    "payment_collection":  N_("تحصيل المدفوعات"),
    "admin_pricing":       N_("تسعير الإدارة"),

    # ── التقارير والتدقيق ──
    "reports":             N_("التقارير"),
    "audit":               N_("التدقيق"),
    "audit_logs":          N_("سجلّ التدقيق"),
    "operational_reports": N_("التقارير التشغيلية"),
    "events":              N_("الأحداث"),

    # ── الشبكة والمايكروتيك ──
    "network":             N_("الشبكة والمايكروتيك"),
    "nas":                 N_("أجهزة NAS"),
    "routers":             N_("الراوترات"),
    "devices":             N_("أجهزة الشبكة"),
    "device_health":       N_("تتبّع صحة الأجهزة"),
    "mt_topology":         N_("خريطة الشبكة"),
    "mt_login_designer":   N_("مصمّم صفحة الدخول"),
    "mt_diagnostics":      N_("تشخيص المايكروتيك"),
    "network_policy":      N_("سياسات الشبكة"),
    "site_exit":           N_("مخرج الموقع"),
    "pools":               N_("نطاقات العناوين"),
    "monitoring":          N_("المراقبة والصحة"),
    "router_alerts":       N_("تنبيهات الراوترات"),
    "router_metrics":      N_("مقاييس الراوترات"),

    # ── الأمان والتحكم بالدخول ──
    "access_control":      N_("التحكم بالدخول"),
    "anti_mac_clone":      N_("منع استنساخ MAC"),
    "security":            N_("الأمان"),

    # ── الإدارة والإعدادات ──
    "admins":              N_("المدراء والصلاحيات"),
    "settings":            N_("الإعدادات"),
    "tenants":             N_("المستأجرون"),
    "sections":            N_("إدارة أقسام الواجهة"),
    "backups":             N_("النسخ الاحتياطية"),
    "recycle_bin":         N_("سلّة المحذوفات"),
    "lifecycle":           N_("دورة الحياة"),
    "system":              N_("النظام"),
    "tools":               N_("الأدوات"),
    "tokens":              N_("مفاتيح API"),
    "webhooks":            N_("إشعارات الربط"),
    "share_groups":        N_("مجموعات المشاركة"),
    "business_os":         N_("أعمال HobeOS"),
    "print_templates":     N_("قوالب الطباعة"),
    "hotspot_designs":     N_("تصاميم الهوتسبوت"),

    # ── الاتصالات والإشعارات ──
    "communications":      N_("الرسائل والتنبيهات"),
    "messaging":           N_("الرسائل"),
    "notifications":       N_("الإشعارات"),
    "alerts":              N_("التنبيهات"),
    "admin_alerts":        N_("تنبيهات الإدارة"),
    "telegram":            N_("تلجرام"),
    "whatsapp":            N_("واتساب"),
    "whatsapp_bot":        N_("بوت واتساب"),
    "network_telegram":    N_("تلجرام الشبكة"),

    # ── البوّابات والخدمات الذاتية ──
    "customer_portal":     N_("بوّابة المشترك"),
    "subscriber_portal":   N_("بوّابة المشترك"),
    "customer_portals":    N_("بوّابات الزبائن"),
    "service_requests":    N_("طلبات الخدمات"),
    "tickets":             N_("تذاكر الدعم"),

    # ── المتجر والموزّعون ──
    "store":               N_("المتجر"),
    "store_admin":         N_("إدارة المتجر"),
    "store_support":       N_("دعم المتجر"),
    "distributors":        N_("الموزّعون"),
    "marketplace":         N_("السوق"),

    # ── الجلسات والتشغيل ──
    "sessions":            N_("الجلسات"),
    "online":              N_("المتّصلون الآن"),
    "live_session_control":N_("التحكّم الحيّ بالجلسات"),

    # ── مكوّنات النظام الأخرى ──
    "dashboard":           N_("لوحة المعلومات"),
    "setup_wizard":        N_("معالج الإعداد"),
    "license_admin":       N_("إدارة الترخيص"),
    "admin_bridge":        N_("جسر الإدارة"),
    "internal_auth":       N_("المصادقة الداخلية"),
    "health":              N_("الصحة"),
    "i18n":                N_("تعدّد اللغات"),

    # ── مفاتيح كتالوج المزوّد الإضافية ──
    "customer_support":         N_("الدعم والتذاكر"),
    "integration_bridge":       N_("جسر التكامل"),
    "integration_tokens":       N_("مفاتيح الواجهة"),
    # «تغيير عنوان الإنترنت» — ONE merged service (provider sends ip_change_vpn);
    # public_ip_change stays as the merged-in server-public-IP backend method key.
    "ip_change_vpn":            N_("تغيير عنوان الإنترنت"),
    "ip_pools":                 N_("نطاقات العناوين"),
    "loop_detection":           N_("كشف اللوب"),
    "multi_tenant":             N_("الجهات (المستأجرون)"),
    "network_policies":         N_("سياسات الشبكة"),
    "operations_center":        N_("مركز العمليات"),
    "public_ip_change":         N_("تغيير عنوان الإنترنت — IP العام للخادم"),
    "radius_customer_portals":  N_("بوابات عملاء الريدياس"),
    "remote_access":            N_("الوصول البعيد"),
    "remote_health_fix":        N_("صيانة عن بعد"),
    "remote_support":           N_("دعم فني عن بعد"),
    "risk_events":              N_("الأحداث والمخاطر"),
    "router_diagnostics":       N_("تشخيص الراوترات"),
    "sms_gateway":              N_("بوابة SMS"),
    "whatsapp_gateway":         N_("واتساب"),
}


# ─────────────────────────────────────────────────────────────────────
# تسميات الحالات (status raw → عربي)
# ─────────────────────────────────────────────────────────────────────
SERVICE_STATUS_AR: dict[str, str] = {
    # نشطة
    "active":               N_("مفعّلة"),
    "valid":                N_("صالحة"),
    "ok":                   N_("سليمة"),
    "healthy":              N_("سليمة"),
    "grace":                N_("ضمن سماحية"),
    # موقوفة (هارد-سَوسبَند)
    "disabled":             N_("موقوفة"),
    "suspended":            N_("معلَّقة"),
    "expired":              N_("منتهية"),
    "cancelled":            N_("ملغاة"),
    "revoked":              N_("مسحوبة"),
    "denied":               N_("ممنوعة"),
    "blocked":              N_("محظورة"),
    "inactive":             N_("غير مفعّلة"),
    "not_found":            N_("غير موجودة"),
    "invalid_request":      N_("طلب غير صالح"),
    "fingerprint_denied":   N_("بصمة مرفوضة"),
    # locked_upgrade — مدفوعة-غير-مفعّلة
    "locked_upgrade":       N_("بانتظار التفعيل / ترقية"),
    "requires_activation":  N_("بانتظار التفعيل"),
    "requires_upgrade":     N_("بحاجة ترقية"),
    "upgrade_required":     N_("بحاجة ترقية"),
    "paid_not_active":      N_("مدفوعة — لم تُفعَّل"),
    "paid_locked":          N_("مدفوعة — مقفلة"),
    "pending_activation":   N_("بانتظار التفعيل"),
    "not_purchased":        N_("لم تُشترَ بعد"),
    # أخرى محايدة
    "unknown":              N_("غير معروفة"),
    "stale":                N_("قديمة"),
}


# ─────────────────────────────────────────────────────────────────────
# تسميات حالة الميزة (features.<k> → عربي)
# ─────────────────────────────────────────────────────────────────────
FEATURE_STATE_AR: dict[str, str] = {
    "enabled":         N_("متاحة"),
    "locked":          N_("مقفلة"),
    "hidden":          N_("مخفية"),
    "readonly":        N_("قراءة فقط"),
    "read_only":       N_("قراءة فقط"),
    "locked_upgrade":  N_("قفل ترقية"),
    "requires_activation": N_("بانتظار التفعيل"),
    "upgrade_required": N_("بحاجة ترقية"),
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
