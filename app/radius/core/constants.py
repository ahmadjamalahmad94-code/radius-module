"""
ثوابت HobeRadius — domain values فقط (لا env).
"""
from __future__ import annotations

# ────────────────────────── حالات عامة ──────────────────────────

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_EXPIRED = "expired"
STATUS_SUSPENDED = "suspended"
STATUS_PENDING = "pending"

ACCOUNT_STATUSES = (STATUS_ENABLED, STATUS_DISABLED, STATUS_EXPIRED, STATUS_SUSPENDED, STATUS_PENDING)

# ────────────────────────── أوضاع الـ Adapter ──────────────────────────

ADAPTER_MODE_MANUAL = "manual"
ADAPTER_MODE_API = "api"
ADAPTER_MODE_DIRECT = "direct"
ADAPTER_MODE_SQLITE = "sqlite"

ADAPTER_MODES = (ADAPTER_MODE_MANUAL, ADAPTER_MODE_API, ADAPTER_MODE_DIRECT, ADAPTER_MODE_SQLITE)

# ────────────────────────── أنواع NAS ──────────────────────────

NAS_VENDOR_MIKROTIK = "mikrotik"
NAS_VENDOR_CISCO = "cisco"
NAS_VENDOR_HUAWEI = "huawei"
NAS_VENDOR_UBIQUITI = "ubiquiti"
NAS_VENDOR_OTHER = "other"
NAS_VENDORS = (NAS_VENDOR_MIKROTIK, NAS_VENDOR_CISCO, NAS_VENDOR_HUAWEI, NAS_VENDOR_UBIQUITI, NAS_VENDOR_OTHER)

NAS_TYPE_HOTSPOT = "hotspot"
NAS_TYPE_PPPOE = "pppoe"
NAS_TYPE_DHCP = "dhcp"
NAS_TYPE_OTHER = "other"
NAS_TYPES = (NAS_TYPE_HOTSPOT, NAS_TYPE_PPPOE, NAS_TYPE_DHCP, NAS_TYPE_OTHER)

# ────────────────────────── أنواع الباقات/العروض ──────────────────────────

PLAN_TYPE_TIME = "time"
PLAN_TYPE_QUOTA = "quota"
PLAN_TYPE_HYBRID = "hybrid"
PLAN_TYPE_UNLIMITED = "unlimited"
PLAN_TYPE_RECURRING = "recurring"
PLAN_TYPES = (PLAN_TYPE_TIME, PLAN_TYPE_QUOTA, PLAN_TYPE_HYBRID, PLAN_TYPE_UNLIMITED, PLAN_TYPE_RECURRING)

# ────────────────────────── أنواع المستخدمين ──────────────────────────

USER_TYPE_SUBSCRIBER = "subscriber"
USER_TYPE_CARD = "card"
USER_TYPE_TRIAL = "trial"
USER_TYPES = (USER_TYPE_SUBSCRIBER, USER_TYPE_CARD, USER_TYPE_TRIAL)

# ────────────────────────── سجلات التدقيق ──────────────────────────

AUDIT_ACTION_CREATE = "create"
AUDIT_ACTION_UPDATE = "update"
AUDIT_ACTION_DELETE = "delete"
AUDIT_ACTION_DISCONNECT = "disconnect"
AUDIT_ACTION_RESET_PASSWORD = "reset_password"
AUDIT_ACTION_DISABLE = "disable"
AUDIT_ACTION_ENABLE = "enable"
AUDIT_ACTION_LOGIN = "login"
AUDIT_ACTION_LOGOUT = "logout"
AUDIT_ACTION_BATCH_GENERATE = "batch_generate"
AUDIT_ACTION_REVOKE = "revoke"
AUDIT_ACTION_ARCHIVE = "archive"
AUDIT_ACTION_RESTORE = "restore"
AUDIT_ACTION_VOID = "void"
AUDIT_ACTION_REVERSAL = "reversal"
AUDIT_ACTION_SETTLEMENT = "settlement"
AUDIT_ACTION_LOAN_GRANT = "loan_grant"
AUDIT_ACTION_LOAN_SETTLE = "loan_settle"
AUDIT_ACTION_PAYMENT_RECORD = "payment_record"
AUDIT_ACTION_CARD_CHECK = "card_check"
AUDIT_ACTION_BATCH_ARCHIVE = "batch_archive"
AUDIT_ACTION_BATCH_RESTORE = "batch_restore"
AUDIT_ACTION_LEDGER_POST = "ledger_post"
AUDIT_ACTION_LEDGER_VOID = "ledger_void"

AUDIT_SCHEMA_CUSTOMER_ROADMAP_V1 = "customer-roadmap.audit.v1"

CUSTOMER_ROADMAP_AUDIT_ACTIONS: tuple[str, ...] = (
    AUDIT_ACTION_ARCHIVE,
    AUDIT_ACTION_RESTORE,
    AUDIT_ACTION_VOID,
    AUDIT_ACTION_REVERSAL,
    AUDIT_ACTION_SETTLEMENT,
    AUDIT_ACTION_LOAN_GRANT,
    AUDIT_ACTION_LOAN_SETTLE,
    AUDIT_ACTION_PAYMENT_RECORD,
    AUDIT_ACTION_CARD_CHECK,
    AUDIT_ACTION_BATCH_ARCHIVE,
    AUDIT_ACTION_BATCH_RESTORE,
    AUDIT_ACTION_LEDGER_POST,
    AUDIT_ACTION_LEDGER_VOID,
)

LEDGER_TYPE_PAYMENT = "payment"
LEDGER_TYPE_LOAN = "loan"
LEDGER_TYPE_SETTLEMENT = "settlement"
LEDGER_TYPE_VOID = "void"
LEDGER_TYPE_REVERSAL = "reversal"
LEDGER_TYPE_CORRECTION = "correction"
LEDGER_TYPES: tuple[str, ...] = (
    LEDGER_TYPE_PAYMENT,
    LEDGER_TYPE_LOAN,
    LEDGER_TYPE_SETTLEMENT,
    LEDGER_TYPE_VOID,
    LEDGER_TYPE_REVERSAL,
    LEDGER_TYPE_CORRECTION,
)

LEDGER_STATUS_POSTED = "posted"
LEDGER_STATUS_VOID = "void"
LEDGER_STATUS_REVERSAL = "reversal"
LEDGER_STATUSES: tuple[str, ...] = (
    LEDGER_STATUS_POSTED,
    LEDGER_STATUS_VOID,
    LEDGER_STATUS_REVERSAL,
)

LOAN_STATUS_OPEN = "open"
LOAN_STATUS_SETTLED = "settled"
LOAN_STATUS_VOIDED = "voided"
LOAN_STATUSES: tuple[str, ...] = (
    LOAN_STATUS_OPEN,
    LOAN_STATUS_SETTLED,
    LOAN_STATUS_VOIDED,
)

PAYMENT_STATUS_POSTED = "posted"
PAYMENT_STATUS_VOIDED = "voided"

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_DELETED = "deleted"
LIFECYCLE_ARCHIVED = "archived"
LIFECYCLE_CANCELLED = "cancelled"
LIFECYCLE_STATUSES: tuple[str, ...] = (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_DELETED,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_CANCELLED,
)

# ────────────────────────── السياسات ──────────────────────────

POLICY_TYPE_RATE_LIMIT = "rate_limit"
POLICY_TYPE_QUOTA = "quota"
POLICY_TYPE_TIME_WINDOW = "time_window"
POLICY_TYPE_CONCURRENT = "concurrent_sessions"
POLICY_TYPES = (POLICY_TYPE_RATE_LIMIT, POLICY_TYPE_QUOTA, POLICY_TYPE_TIME_WINDOW, POLICY_TYPE_CONCURRENT)

# ────────────────────────── الأدوار والصلاحيات ──────────────────────────

PERM_DASHBOARD_VIEW = "dashboard.view"

PERM_USERS_VIEW = "users.view"
PERM_USERS_CREATE = "users.create"
PERM_USERS_EDIT = "users.edit"
PERM_USERS_DELETE = "users.delete"
PERM_USERS_DISCONNECT = "users.disconnect"

PERM_CARDS_VIEW = "cards.view"
PERM_CARDS_GENERATE = "cards.generate"
PERM_CARDS_REVOKE = "cards.revoke"

PERM_PLANS_VIEW = "plans.view"
PERM_PLANS_CREATE = "plans.create"
PERM_PLANS_EDIT = "plans.edit"
PERM_PLANS_DELETE = "plans.delete"

PERM_NAS_VIEW = "nas.view"
PERM_NAS_CREATE = "nas.create"
PERM_NAS_EDIT = "nas.edit"
PERM_NAS_DELETE = "nas.delete"

PERM_SESSIONS_VIEW = "sessions.view"
PERM_SESSIONS_DISCONNECT = "sessions.disconnect"

PERM_ADMINS_VIEW = "admins.view"
PERM_ADMINS_CREATE = "admins.create"
PERM_ADMINS_EDIT = "admins.edit"
PERM_ADMINS_DELETE = "admins.delete"

PERM_SETTINGS_VIEW = "settings.view"
PERM_SETTINGS_EDIT = "settings.edit"
PERM_AUDIT_VIEW = "audit.view"
PERM_API_USE = "api.use"

# ─── صلاحيات تشغيلية دقيقة للمشتركين (توسعة 2026-06) ───
# كل مفتاح هنا يطابق endpoints فعلية في routes/users.py + accounting.py —
# لا مفاتيح لميزات غير موجودة. التحكّم الدقيق بكل حركة على المشترك.
PERM_USERS_CHANGE_STATUS = "users.change_status"   # تفعيل/تعطيل (users_toggle/_bulk)
PERM_USERS_EXTEND = "users.extend"                 # تجديد/تمديد فردي وجماعي (users_extend/_bulk)
PERM_USERS_CHANGE_PLAN = "users.change_plan"       # تغيير الباقة (users_change_plan)
PERM_USERS_QUOTA = "users.quota"                   # إضافة كوتا + تصفير يومي (quota_topup/reset_daily ±bulk)
PERM_USERS_BALANCE_ADD = "users.balance_add"       # إضافة رصيد للمشترك (users_balance_add/_bulk)
PERM_USERS_PAYMENTS = "users.payments"             # شحن نقدي/دفعات (users_payment_create/_bulk)
PERM_USERS_LOANS = "users.loans"                   # سلفة/تفعيل بالدَّين + تسوية (users_loan_*)
PERM_USERS_SEND_MESSAGE = "users.send_message"     # إرسال رسالة/SMS (users_send_sms/_bulk)
PERM_USERS_TEMP_SPEED = "users.temp_speed"         # السرعة المؤقتة (online_temp_speed + cancel)
PERM_USERS_EXPORT = "users.export"                 # تصدير الجداول (export_table)

# ─── المتصلون الآن (شاشة /online) — مفاتيح مستقلة عن sessions.* القديمة ───
PERM_ONLINE_VIEW = "online.view"                   # عرض شاشة المتصلين (online_list)
PERM_ONLINE_DISCONNECT = "online.disconnect"       # قطع اتصال (online_disconnect)
PERM_ONLINE_LOCK_MAC = "online.lock_mac"           # إغلاق على MAC (online_lock_mac)
PERM_ONLINE_LOCK_IP = "online.lock_ip"             # تثبيت IP (online_lock_ip)

# ─── عمليات البطاقات الدقيقة (routes/cards.py + recharge + print) ───
PERM_CARDS_EDIT_BATCH = "cards.edit_batch"         # تعديل حزمة (cards_batch_edit)
PERM_CARDS_BATCH_OPS = "cards.batch_ops"           # عمليات جماعية: أرشفة/استعادة/تفعيل/تعطيل (cards_batches_bulk + cards_batch_cards_actions)
PERM_CARDS_IMPORT = "cards.import"                 # استيراد بطاقات (cards_batches_import)
PERM_CARDS_VERIFY = "cards.verify"                 # فاحص البطاقات: كل عمليات الفحص والتعديل على بطاقة مفردة
                                                   # (cards_checker POST: تفعيل/تعطيل/تثبيت MAC/تعديل وقت/سرعة/حذف ناعم)
PERM_CARDS_RESTORE = "cards.restore"               # استعادة من سلة المحذوفات (recycle_bin_restore)
PERM_CARDS_RECHARGE = "cards.recharge"             # حزم بطاقات الشحن (cards_recharge_*)
PERM_CARDS_PRINT = "cards.print"                   # حزم الطباعة (cards_print_*)

# ─── أسعار العروض للمدراء (/admins/pricing — migration 098) ───
PERM_ADMIN_PRICING_VIEW = "admin_pricing.view"     # عرض صفحة الأسعار (admin_pricing_page)
PERM_ADMIN_PRICING_EDIT = "admin_pricing.edit"     # حفظ سعر خاص (admin_pricing_save)
PERM_ADMIN_PRICING_RESET = "admin_pricing.reset"   # إعادة للسعر الرسمي (admin_pricing_reset/_all)

# ─── محافظ المدراء/الموزعين (business operators) ───
PERM_ADMINS_DEPOSIT = "admins.deposit_balance"     # شحن محفظة مشغّل (business_operator_recharge)
PERM_ADMINS_POLICY = "admins.policy"               # ضبط صلاحيات/حدود المشغّل (business_operator_policy)

# ─── التقارير ───
PERM_REPORTS_VIEW = "reports.view"                 # كل صفحات /reports (الجلسات، MAC، أحداث المدراء…)
PERM_REPORTS_FINANCE = "reports.finance"           # دفتر القيود والتقارير المالية (finance_ledger/finance_reports)

# ─── المتجر الإلكتروني المتقدّم — مجموعة صلاحيّات مستقلّة (store.*) ───
# قبل هذه المجموعة كان المتجر بلا مفاتيح خاصّة: العرض مستعار من
# cards.view (فيَظهر لأيّ من يرى البطاقات — تسريب)، وكلّ الكتابة مستعارة
# من cards.recharge. الآن لكلّ فعل مفتاحه المستقلّ، فيَقدر المالك يَمنح
# «عرض المتجر» أو «إضافة باقة» أو «تعديل مستخدم» … كلًّا على حِدة. السوبر
# أدمن/المالك الرئيسيّ يتجاوز دائمًا. حركة المال (الشحن/الشراء/تأكيد
# الإيداع) لها مفاتيحها المالية المنفصلة.
PERM_STORE_VIEW = "store.view"                     # عرض المتجر: السوق + مستخدمو البطاقات + تفاصيل العرض (يَفصله عن cards.view)
PERM_STORE_PACKAGE_ADD = "store.package_add"       # إضافة/إدارة باقة إلكترونيّة (إنشاء + وضع البيع + رفع المخزون)
PERM_STORE_USER_ADD = "store.user_add"             # إنشاء حساب مستخدم متجر
PERM_STORE_USER_EDIT = "store.user_edit"           # تعديل مستخدم المتجر (كلمة المرور)
PERM_STORE_USER_RECHARGE = "store.user_recharge"   # شحن محفظة مستخدم المتجر (حركة مال)
PERM_STORE_USER_PURCHASE = "store.user_purchase"   # الشراء بالنيابة عن المستخدم (حركة مال)
PERM_STORE_USER_DELETE = "store.user_delete"       # حذف/استعادة مستخدم المتجر (حذف ناعم: status=archived)
PERM_STORE_REVIEW = "store.review"                 # دعم طلبات الشحن: تأكيد/رفض الإيداع والسحب + القنوات + الشات

# ─── النطاق والإشراف (scope.*) — مفاتيح معرَّفة للتوسع المرحلي ───
# تنبيه: لا يوجد بعدُ فحص مِلكية (manager_id) في طبقة المسارات؛ هذه
# المفاتيح مسجَّلة الآن في الكتالوج وتُمنح/تُحجب من الأدوار، وسيُفعَّل
# فرضها في الخدمات عند بناء فلترة المِلكية — لا تخترع فحوصًا هنا.
PERM_SCOPE_ACT_NON_OWNED = "scope.act_non_owned"           # التصرّف بمشترك غير تابع للمدير
PERM_SCOPE_VIEW_ALL_SUBSCRIBERS = "scope.view_all_subscribers"  # رؤية كل المشتركين لا مشتركيه فقط
PERM_SCOPE_VIEW_ALL_MANAGERS = "scope.view_all_managers"   # رؤية كل المدراء
PERM_SCOPE_VIEW_ALL_CARDS = "scope.view_all_cards"         # رؤية كل البطاقات
PERM_SCOPE_VIEW_ALL_REPORTS = "scope.view_all_reports"     # رؤية تقارير كل المدراء
PERM_SCOPE_VIEW_PASSWORDS = "scope.view_passwords"         # كشف كلمات سر المشتركين/البطاقات
                                                           # (cards_checker_api_reveal_password)

# ─── إدارة الراوترات عن بُعد (TR-069) — وحدة تجريبيّة (routers.*) ───
PERM_ROUTERS_VIEW = "routers.view"                      # رؤية قائمة/تفاصيل الراوترات
PERM_ROUTERS_MANAGE = "routers.manage"                  # تسجيل/ربط/تعديل الراوتر
PERM_ROUTERS_REBOOT = "routers.reboot"                  # إعادة تشغيل
PERM_ROUTERS_CHANGE_WIFI = "routers.change_wifi"        # تغيير Wi-Fi
PERM_ROUTERS_CHANGE_PPPOE = "routers.change_pppoe"      # تغيير PPPoE
PERM_ROUTERS_UPDATE_FIRMWARE = "routers.update_firmware"  # تحديث Firmware
PERM_ROUTERS_FACTORY_RESET = "routers.factory_reset"    # إعادة ضبط المصنع (خطر)
PERM_ROUTERS_VIEW_SENSITIVE = "routers.view_sensitive_data"  # كشف بيانات CWMP الحسّاسة

ALL_PERMISSIONS: tuple[str, ...] = (
    PERM_DASHBOARD_VIEW,
    PERM_USERS_VIEW, PERM_USERS_CREATE, PERM_USERS_EDIT, PERM_USERS_DELETE, PERM_USERS_DISCONNECT,
    PERM_USERS_CHANGE_STATUS, PERM_USERS_EXTEND, PERM_USERS_CHANGE_PLAN,
    PERM_USERS_QUOTA, PERM_USERS_BALANCE_ADD, PERM_USERS_PAYMENTS, PERM_USERS_LOANS,
    PERM_USERS_SEND_MESSAGE, PERM_USERS_TEMP_SPEED, PERM_USERS_EXPORT,
    PERM_ONLINE_VIEW, PERM_ONLINE_DISCONNECT, PERM_ONLINE_LOCK_MAC, PERM_ONLINE_LOCK_IP,
    PERM_CARDS_VIEW, PERM_CARDS_GENERATE, PERM_CARDS_REVOKE,
    PERM_CARDS_EDIT_BATCH, PERM_CARDS_BATCH_OPS, PERM_CARDS_IMPORT, PERM_CARDS_VERIFY,
    PERM_CARDS_RESTORE, PERM_CARDS_RECHARGE, PERM_CARDS_PRINT,
    PERM_PLANS_VIEW, PERM_PLANS_CREATE, PERM_PLANS_EDIT, PERM_PLANS_DELETE,
    PERM_NAS_VIEW, PERM_NAS_CREATE, PERM_NAS_EDIT, PERM_NAS_DELETE,
    PERM_SESSIONS_VIEW, PERM_SESSIONS_DISCONNECT,
    PERM_ADMINS_VIEW, PERM_ADMINS_CREATE, PERM_ADMINS_EDIT, PERM_ADMINS_DELETE,
    PERM_ADMINS_DEPOSIT, PERM_ADMINS_POLICY,
    PERM_ADMIN_PRICING_VIEW, PERM_ADMIN_PRICING_EDIT, PERM_ADMIN_PRICING_RESET,
    PERM_REPORTS_VIEW, PERM_REPORTS_FINANCE,
    PERM_STORE_VIEW, PERM_STORE_PACKAGE_ADD, PERM_STORE_USER_ADD, PERM_STORE_USER_EDIT,
    PERM_STORE_USER_RECHARGE, PERM_STORE_USER_PURCHASE, PERM_STORE_USER_DELETE, PERM_STORE_REVIEW,
    PERM_SCOPE_ACT_NON_OWNED, PERM_SCOPE_VIEW_ALL_SUBSCRIBERS, PERM_SCOPE_VIEW_ALL_MANAGERS,
    PERM_SCOPE_VIEW_ALL_CARDS, PERM_SCOPE_VIEW_ALL_REPORTS, PERM_SCOPE_VIEW_PASSWORDS,
    PERM_ROUTERS_VIEW, PERM_ROUTERS_MANAGE, PERM_ROUTERS_REBOOT,
    PERM_ROUTERS_CHANGE_WIFI, PERM_ROUTERS_CHANGE_PPPOE, PERM_ROUTERS_UPDATE_FIRMWARE,
    PERM_ROUTERS_FACTORY_RESET, PERM_ROUTERS_VIEW_SENSITIVE,
    PERM_SETTINGS_VIEW, PERM_SETTINGS_EDIT, PERM_AUDIT_VIEW,
    PERM_API_USE,
)

ROLE_SUPER_ADMIN = "super_admin"
# MT23 — «مدير الشبكة»: مدير كامل لجهته في منتج الاستضافة (يضيف راوترات،
# يولّد سكربتات، يدير مشتركين/كروت/باقات...). يحمل كل الصلاحيات لكنه ليس
# سوبر/مالكًا — فأسطح المزوّد (_PERM_SUPER) والجهات الأخرى تبقى محجوبة عنه.
ROLE_NETWORK_ADMIN = "network_admin"
ROLE_OPERATOR = "operator"
ROLE_SUPPORT = "support"
ROLE_BILLING = "billing"
ROLE_VIEWER = "viewer"

DEFAULT_ROLE_PERMISSIONS = {
    ROLE_SUPER_ADMIN: tuple(ALL_PERMISSIONS),
    ROLE_NETWORK_ADMIN: tuple(ALL_PERMISSIONS),
    ROLE_OPERATOR: (
        PERM_DASHBOARD_VIEW,
        PERM_USERS_VIEW, PERM_USERS_CREATE, PERM_USERS_EDIT, PERM_USERS_DISCONNECT,
        # المفاتيح التشغيلية الدقيقة — المشغّل اليومي يحتاجها كلها
        PERM_USERS_CHANGE_STATUS, PERM_USERS_EXTEND, PERM_USERS_CHANGE_PLAN,
        PERM_USERS_QUOTA, PERM_USERS_SEND_MESSAGE, PERM_USERS_TEMP_SPEED, PERM_USERS_EXPORT,
        PERM_ONLINE_VIEW, PERM_ONLINE_DISCONNECT, PERM_ONLINE_LOCK_MAC, PERM_ONLINE_LOCK_IP,
        PERM_CARDS_VIEW, PERM_CARDS_GENERATE, PERM_CARDS_VERIFY,
        PERM_PLANS_VIEW, PERM_NAS_VIEW,
        PERM_SESSIONS_VIEW, PERM_SESSIONS_DISCONNECT,
        PERM_REPORTS_VIEW,
        PERM_AUDIT_VIEW,
    ),
    ROLE_SUPPORT: (
        PERM_DASHBOARD_VIEW,
        PERM_USERS_VIEW, PERM_USERS_DISCONNECT,
        PERM_ONLINE_VIEW, PERM_ONLINE_DISCONNECT,
        PERM_CARDS_VIEW, PERM_PLANS_VIEW, PERM_NAS_VIEW,
        PERM_SESSIONS_VIEW, PERM_SESSIONS_DISCONNECT,
    ),
    ROLE_BILLING: (PERM_DASHBOARD_VIEW, PERM_USERS_VIEW, PERM_CARDS_VIEW, PERM_PLANS_VIEW,
                   PERM_STORE_REVIEW),
    ROLE_VIEWER: (PERM_DASHBOARD_VIEW,),
}

# ────────────────────────── حدود نمو ──────────────────────────

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
TABLE_FETCH_CAP = 2000
