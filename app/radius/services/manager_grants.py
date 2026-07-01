"""Granular per-manager grants — owner-configured, server-enforced.

هذا المصدر الموحّد لنظام صلاحيات المدير الدقيق الذي يَضبطه المالك من صفحة
«الصلاحيات والحدود» لكل مدير (``/business-operators/manager/<id>``). ثلاثة
مستويات، كلها مُخزَّنة على صفّ السياسة الموجود أصلًا
(``manager_distributor_policies``) — نَبني على المخزن القائم ولا نَفرع نظامًا
موازيًا:

  1. **وصول القسم (3 حالات)**: ``open`` (مفتوح) / ``locked`` (مقفول — ظاهر
     للعرض فقط) / ``hidden`` (مخفي). القسم غير المُهيّأ = ``open`` (غير
     انحداريّ: RBAC الدور يَبقى الحاكم حتى يَقفل/يُخفي المالك القسم صراحةً).
  2. **بوّابة الفعل**: create / edit / delete داخل قسم مفتوح (المستوى 2).
  3. **التحكّم الحقليّ**: أيّ الحقول بالضبط يَملك المدير تغييرها (المستوى 3).

المالك الرئيسي/السوبر يَتجاوز المستويات الثلاثة دائمًا (نفس عقد
``session_helpers._resolve_is_super`` و[[owner-only-bypass]]).

**سجلّ الأقسام قابل للتوسعة**: إضافة قسم = إدخال في ``MANAGER_SECTION_REGISTRY``
(القسم → endpointات + تصنيف endpointات العرض). أيّ endpoint غير مُدرَج لا
تُؤثّر عليه أعلام الأقسام (يَخضع لـRBAC العاديّ فقط).

كل القراءات مخزَّنة لكل طلب في ``flask.g`` — لا استعلام DB إضافيّ لكل بند.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from flask import g


# ─── قيم حالة القسم الثلاث ────────────────────────────────────────────────
OPEN = "open"
LOCKED = "locked"
HIDDEN = "hidden"
SECTION_STATES = (OPEN, LOCKED, HIDDEN)
# غير المُهيّأ = مفتوح (غير انحداريّ): المالك يَقفل/يُخفي صراحةً.
DEFAULT_SECTION_STATE = OPEN


# ─── سجلّ الأقسام: قسم منطقيّ → endpointات تنتمي إليه ────────────────────
# ``endpoints`` = كل endpointات القسم (عرضًا وكتابةً). عند «إخفاء» القسم
# تُحجب كلها (403 لأيّ method)؛ عند «قفله» تُحجب الكتابة فقط (403 لغير GET).
# القوائم غير حصريّة تمامًا لكنها تُغطّي بنود الشريط الجانبي والمسارات
# الحسّاسة لكل قسم — أضِف endpointات جديدة هنا عند الحاجة.
MANAGER_SECTION_REGISTRY: dict[str, dict[str, Any]] = {
    "subscribers": {
        "label": "المشتركون",
        "icon": "users",
        "view_perm": "users.view",
        "endpoints": (
            # عرض
            "subscribers_overview", "subscribers_list", "users_list", "users_new",
            "users_edit", "users_profile", "users_360", "subscriber_360",
            "subscriber_groups_list",
            "rep_login_states_subscribers", "rep_subscriber_consumption",
            # كتابة/إجراء
            "users_create", "users_update", "users_delete", "users_bulk_delete",
            "users_toggle", "users_toggle_bulk", "users_extend", "users_extend_bulk",
            "users_change_plan", "users_quota_topup", "users_quota_topup_bulk",
            "users_quota_reset_daily", "users_quota_reset_daily_bulk",
            "users_balance_add", "users_balance_add_bulk",
            "users_send_sms", "users_send_sms_bulk", "users_send_credentials",
            "users_payment_create", "users_payment_create_bulk",
            "users_loan_create", "users_loan_create_bulk", "users_loan_settle",
            "users_temp_speed_cancel",
            "subscriber_groups_create", "subscriber_groups_update",
            "subscriber_groups_delete",
        ),
    },
    # الجلسات / المتصلون الآن — عائلة أفعال «المتصلون» مُفرَدة بقسمها الخاصّ
    # (نُقِلت من قسم المشتركين) ليَضبط المالك كل فعلٍ بحدة.
    "sessions": {
        "label": "الجلسات / المتصلون",
        "icon": "wifi",
        "view_perm": "online.view",
        "endpoints": (
            "online_list", "online_live_status", "connected_stats",
            "connected_stats_json",
            "online_reconcile", "online_disconnect", "online_force_close",
            "online_lock_mac", "online_lock_ip",
            "online_temp_speed", "online_temp_speed_cancel",
            "online_coa_set_ip", "online_coa_set_speed",
        ),
    },
    "cards": {
        "label": "البطاقات",
        "icon": "id-card",
        "view_perm": "cards.view",
        "endpoints": (
            # عرض
            "cards_overview", "cards_checker", "cards_checker_v2", "cards_batches",
            "cards_generate", "cards_offers", "cards_print_list", "print_templates",
            "cards_list", "card_marketplace", "card_users_list", "cards_recharge_list",
            "rep_login_states_cards", "cards_batches_export_csv",
            "cards_batches_export_pdf", "cards_batches_export_xlsx",
            "card_users_add",
            # كتابة/إجراء
            "cards_batch_edit", "cards_batches_bulk", "cards_batch_cards_actions",
            "cards_generate_progress_start", "cards_revoke", "cards_offer_use",
            "cards_recharge_new", "cards_recharge_batch_delete",
            "cards_print_new", "cards_print_batch_delete",
            "cards_import", "cards_import_analyze",
        ),
    },
    "plans": {
        "label": "الباقات والسرعات",
        "icon": "tags",
        "view_perm": "plans.view",
        "endpoints": (
            "plans_overview", "plans_list", "plans_new", "bw_list", "bw_new",
            "bandwidth_schedules",
            "plans_create", "plans_update", "plans_delete",
        ),
    },
    "distributors": {
        "label": "الموزّعون",
        "icon": "people-carry-box",
        "view_perm": "reports.finance",
        "endpoints": (
            "distributors_list",
            "distributors_create", "distributors_update",
            "distributors_assign_batch", "distributors_settle",
        ),
    },
    "network": {
        "label": "الشبكة والراوترات",
        "icon": "network-wired",
        "view_perm": "nas.view",
        "endpoints": (
            "devices_list", "devices_new", "mt_operations", "mt_operations_live",
            "services_catalog", "pool_list", "diagnostics", "device_health_page",
            "device_health_api_checks", "device_health_api_list",
            "device_health_api_router_interfaces", "ipchange_page", "sync_list",
            "devices_create", "devices_update", "devices_toggle",
            "devices_bulk_toggle", "devices_delete",
            "network_devices_create", "network_devices_update", "network_devices_delete",
        ),
    },
    "reports": {
        "label": "التقارير",
        "icon": "chart-line",
        "view_perm": "reports.view",
        "endpoints": (
            "reports_home", "reports_financial", "reports_cards",
            "reports_distributors", "reports_archive", "reports_archive_create",
            "rep_sessions", "rep_failed_logins", "rep_login_status",
            "rep_login_states", "rep_mac_history", "rep_profile_changes",
            "rep_api_messages", "rep_coa_failures", "rep_manager_events",
            "rep_manager_login_status", "rep_user_events", "rep_speed_failures",
            "rep_used_cards", "rep_balance_movements", "rep_cash_transactions",
        ),
    },
    "finance": {
        "label": "المال والمحاسبة",
        "icon": "file-invoice-dollar",
        "view_perm": "reports.finance",
        "endpoints": (
            "finance_center_hub", "accounting_hub", "billing_hub",
            "recharge_panel", "company_inventory", "finance_ledger",
            "finance_reports", "finance_reports_snapshot",
            "finance_reports_export_csv", "finance_reports_export_xlsx",
            "finance_reports_export_pdf",
            "business_finance_wallets_create", "business_finance_wallet_credit",
            "business_finance_wallet_debit", "inv_create",
        ),
    },
    "communications": {
        "label": "الاتصالات والحملات",
        "icon": "paper-plane",
        "view_perm": "users.send_message",
        "endpoints": (
            "communications", "communications_send", "communications_templates",
            "communications_campaigns", "whatsapp",
            "users_send_sms", "users_send_sms_bulk",
            "whatsapp_settings", "whatsapp_test", "whatsapp_cloud_test",
        ),
    },
    "store": {
        "label": "المتجر الإلكتروني",
        "icon": "store",
        "view_perm": "store.review",
        "endpoints": (
            "store_support",
            "store_support_deposit_confirm", "store_support_deposit_reject",
            "store_support_withdrawal_confirm", "store_support_withdrawal_reject",
            "store_support_payment_method_create", "store_support_payment_method_update",
            "store_support_chat_post", "store_support_chat_status",
        ),
    },
}


# ─── سجلّ الحقول القابلة للمنح لكل كيان (المستوى 3) — قابل للتوسعة ────────
# كل إدخال: key (مفتاح المنح المُخزَّن)، label (عربيّ)، attrs (أسماء حقول
# الـDTO/النموذج التي يَحكمها هذا المنح). إضافة حقل = سطر واحد؛ إضافة كيان =
# مفتاح جديد. عند تفعيل التحكّم الحقليّ لكيان، تُعاد الحقولُ غيرُ الممنوحة إلى
# قيمتها القائمة خادميًّا (تُتجاهَل أيّ محاولة POST لتغييرها).
FIELD_REGISTRY: dict[str, tuple[dict[str, Any], ...]] = {
    "subscriber": (
        {"key": "name",     "label": "الاسم",         "attrs": ("full_name",)},
        {"key": "password", "label": "كلمة المرور",    "attrs": ("password",)},
        {"key": "mac",      "label": "MAC",            "attrs": ("mac_lock",)},
        {"key": "ip",       "label": "IP",             "attrs": ("static_ip", "pppoe_ip")},
        {"key": "plan",     "label": "العرض/الباقة",   "attrs": ("plan_id",)},
        {"key": "price",    "label": "السعر المخصّص",  "attrs": ("custom_price",)},
        {"key": "status",   "label": "الحالة",         "attrs": ("status",)},
        {"key": "quota",    "label": "الكوتا",         "attrs": ("download_quota_mb",
                                                                  "upload_quota_mb",
                                                                  "combined_quota_mb",
                                                                  "quota_limit_enabled")},
        {"key": "expiry",   "label": "تاريخ الانتهاء", "attrs": ("expire_at",)},
        {"key": "device_count", "label": "عدد الأجهزة", "attrs": ("device_count",
                                                                  "device_limit_mode",
                                                                  "allowed_macs")},
        {"key": "reassign", "label": "نقل المشترك (المدير المسؤول)", "attrs": ("manager_id",)},
        {"key": "speed",    "label": "السرعة",         "attrs": ("bandwidth_control_enabled",
                                                                  "download_speed_kbps",
                                                                  "upload_speed_kbps",
                                                                  "custom_speed")},
    ),
    # عرض البطاقات (card_offers) — attrs = أسماء وسائط update_offer (None=إبقاء).
    # السرعة/الكوتا للعرض مشتقّتان من الباقة المرتبطة (plan) لا أعمدة مستقلّة.
    "offer": (
        {"key": "name",     "label": "الاسم",                 "attrs": ("name",)},
        {"key": "plan",     "label": "الباقة (السرعة/الكوتا)", "attrs": ("plan_id",)},
        {"key": "duration", "label": "المدّة",                "attrs": ("duration_minutes",)},
        {"key": "price",    "label": "السعر",                 "attrs": ("selling", "wholesale")},
    ),
    # الباقة/الحزمة (card_batch) — attrs = مفاتيح dict الخاصّة بـupdate_batch.
    # حقول البنية (count/digits/…) مقفولة دومًا خارج هذا السجلّ
    # (STRUCTURAL_LOCKED_FIELDS) — انظر [[batch-edit-owner-only-structural-lock]].
    "batch": (
        {"key": "name",       "label": "الاسم",           "attrs": ("package_name",)},
        {"key": "plan",       "label": "الباقة",          "attrs": ("plan_id",)},
        {"key": "accounting", "label": "طريقة الاحتساب",  "attrs": ("count_by_seconds",
                                                                    "count_from_first_connect",
                                                                    "duration_mode")},
        {"key": "price",      "label": "السعر",           "attrs": ("price_per_card",
                                                                    "price_bulk",
                                                                    "total_price")},
    ),
}


# ─── سجلّ الأفعال الشامل (المستوى 2) — قابل للتوسعة ───────────────────────
# «كل شيء بصلاحية»: كل عمليّة يُنفّذها المدير مربوطة ببوّابة يَضبطها المالك
# وتُنفَّذ خادميًّا (403 عند الإطفاء) في حارس واحد (_perm_guard خطوة 3c).
# إضافة فعل = إدخال واحد هنا (declarative). كل إدخال:
#   • label / section  : للعرض والتجميع في مصفوفة الإعداد.
#   • endpoints         : كل مسارات الفعل (تُحرَس جميعها).
#   • flag              : مفتاح can_* القائم (يُوحَّد — لا تكرار؛ البوّابة تقرأ
#                         نفس permissions_json). افتراضه OFF (مقيّد).
#   • entity_edit       : فعل «تعديل» لكيان مالكيّ (offer/batch) — يقرأ
#                         action_grants المتداخلة (المرحلة 3)، افتراضه OFF.
#   • default           : للأفعال بلا flag/entity_edit (يَحرسها RBAC أصلًا):
#                         True = مسموح ما لم يُطفئه المالك (يَبقى RBAC حاكمًا،
#                         غير انحداريّ)؛ يُخزَّن الإطفاء الصريح في
#                         action_grants["_actions"][key]=False.
# ملاحظة: بوّابة الفعل **إضافيّة** لا تُضعِف حُرّاس RBAC/المال القائمة
# (_PERM_GUARDED) — تعمل معها فتزيد التقييد فقط. [[qa-rbac-balance-guards-audit]]
ACTION_REGISTRY: dict[str, dict[str, Any]] = {
    # ── المشترك ──
    "subscriber.create": {"label": "إنشاء مشترك", "section": "subscribers",
        "endpoints": ("users_create",), "flag": "can_create_subscriber"},
    "subscriber.delete": {"label": "حذف مشترك", "section": "subscribers",
        "endpoints": ("users_delete", "users_bulk_delete"), "default": True},
    "subscriber.status": {"label": "تفعيل / تعطيل", "section": "subscribers",
        "endpoints": ("users_toggle", "users_toggle_bulk"), "flag": "can_activate_subscriber"},
    "subscriber.extend": {"label": "إضافة وقت / تمديد", "section": "subscribers",
        "endpoints": ("users_extend", "users_extend_bulk"), "default": True},
    "subscriber.renew": {"label": "تجديد", "section": "subscribers",
        "endpoints": ("users_change_plan",), "default": True},
    "subscriber.quota": {"label": "إضافة / استعادة كوتا", "section": "subscribers",
        "endpoints": ("users_quota_topup", "users_quota_topup_bulk",
                      "users_quota_reset_daily", "users_quota_reset_daily_bulk"),
        "default": True},
    "subscriber.balance_add": {"label": "إضافة رصيد / شحن", "section": "subscribers",
        "endpoints": ("users_balance_add", "users_balance_add_bulk"), "default": True},
    "subscriber.payment": {"label": "تسجيل دفعة / تحصيل", "section": "subscribers",
        "endpoints": ("users_payment_create", "users_payment_create_bulk"), "default": True},
    "subscriber.loan": {"label": "منح سلفة", "section": "subscribers",
        "endpoints": ("users_loan_create", "users_loan_create_bulk", "users_loan_settle"),
        "flag": "can_give_loan"},
    "subscriber.free_days": {"label": "منح أيام مجانية", "section": "subscribers",
        "endpoints": (), "flag": "can_give_free_days"},
    "subscriber.trial_days": {"label": "منح أيام تجريبية", "section": "subscribers",
        "endpoints": (), "flag": "can_give_trial_days"},
    "subscriber.send_credentials": {"label": "إرسال بيانات الدخول", "section": "subscribers",
        "endpoints": ("users_send_credentials",), "default": True},
    # ── الاتصالات (المرحلة E — ضبط التكلفة): كل قناة بصلاحيتها، افتراض OFF ──
    "comms.sms": {"label": "إرسال SMS", "section": "communications",
        "endpoints": ("users_send_sms", "users_send_sms_bulk", "communications_send"),
        "default": False},
    "comms.whatsapp": {"label": "إرسال واتساب", "section": "communications",
        "endpoints": ("whatsapp_settings", "whatsapp_test", "whatsapp_cloud_test"),
        "default": False},
    "comms.templates": {"label": "تعديل قوالب الإشعارات", "section": "communications",
        "endpoints": ("communications_templates",), "default": False},
    # ── الجلسات / المتصلون («وسّع المجال»: كل فعلٍ من شاشة المتصلين بصلاحيته) ──
    # نُقِلت أفعال online_* من قسم المشتركين إلى قسم «الجلسات» المستقلّ. افتراضها
    # OFF (مقيّد) — المالك يَمنح كل فعلٍ بحدة. حُرّاس RBAC القائمة تَبقى فوقها.
    "session.edit": {"label": "تعديل الجلسة (IP/سرعة حيّة عبر CoA)", "section": "sessions",
        "endpoints": ("online_coa_set_ip", "online_coa_set_speed"), "default": False},
    "session.lock_mac": {"label": "تثبيت MAC من الجلسة", "section": "sessions",
        "endpoints": ("online_lock_mac",), "default": False},
    "session.lock_ip": {"label": "تثبيت IP من الجلسة", "section": "sessions",
        "endpoints": ("online_lock_ip",), "default": False},
    "session.disconnect": {"label": "قطع جلسة نشطة", "section": "sessions",
        "endpoints": ("online_disconnect",), "default": False},
    "session.force_close": {"label": "إغلاق إجباري للجلسة", "section": "sessions",
        "endpoints": ("online_force_close",), "default": False},
    "session.reconcile": {"label": "مزامنة/تسوية الجلسات", "section": "sessions",
        "endpoints": ("online_reconcile",), "default": False},
    "session.temp_speed": {"label": "سرعة مؤقتة من الجلسة", "section": "sessions",
        "endpoints": ("online_temp_speed", "online_temp_speed_cancel"), "default": False},
    # ── البطاقات ──
    "cards.generate": {"label": "توليد بطاقات", "section": "cards",
        "endpoints": ("cards_generate", "cards_generate_progress_start"),
        "flag": "can_create_batch"},
    "cards.import": {"label": "استيراد حزم", "section": "cards",
        "endpoints": ("cards_import", "cards_import_analyze"), "flag": "can_import_batches"},
    "cards.revoke": {"label": "إبطال بطاقة", "section": "cards",
        "endpoints": ("cards_revoke",), "default": True},
    "cards.batch_ops": {"label": "عمليّات الحزم المجمّعة", "section": "cards",
        "endpoints": ("cards_batches_bulk", "cards_batch_cards_actions"), "default": True},
    "cards.recharge": {"label": "بطاقات شحن مسبق", "section": "cards",
        "endpoints": ("cards_recharge_new", "cards_recharge_batch_delete"), "default": True},
    "cards.print": {"label": "بطاقات طباعة", "section": "cards",
        "endpoints": ("cards_print_new", "cards_print_batch_delete"), "default": True},
    "batch.edit": {"label": "تعديل الحزمة", "section": "cards",
        "endpoints": ("cards_batch_edit",), "entity_edit": "batch"},
    "offer.edit": {"label": "تعديل العرض", "section": "cards",
        "endpoints": ("cards_offer_edit",), "entity_edit": "offer"},
    # ── الباقات ──
    "plan.create": {"label": "إنشاء باقة", "section": "plans",
        "endpoints": ("plans_create",), "default": True},
    "plan.edit": {"label": "تعديل باقة", "section": "plans",
        "endpoints": ("plans_update",), "default": True},
    "plan.delete": {"label": "حذف باقة", "section": "plans",
        "endpoints": ("plans_delete",), "default": True},
    # ── الموزّعون ──
    "distributor.manage": {"label": "إدارة الموزّعين", "section": "distributors",
        "endpoints": ("distributors_create", "distributors_update",
                      "distributors_assign_batch", "distributors_settle"),
        "flag": "can_manage_distributors"},
    # ── تصدير البيانات (المرحلة C) — مسارات GET، لذا نُحرسها على القراءة أيضًا
    # (gate_get). افتراض OFF: المدير غير المُصرَّح لا يُصدِّر CSV/Excel/PDF. ──
    "data.export": {"label": "تصدير البيانات (CSV/Excel/PDF)", "section": "reports",
        "endpoints": ("export_table", "cards_batches_export_csv",
                      "cards_batches_export_pdf", "cards_batches_export_xlsx",
                      "finance_reports_export_csv", "finance_reports_export_xlsx",
                      "finance_reports_export_pdf"),
        "default": False, "gate_get": True},
    # ── المتجر الإلكترونيّ («وسّع المجال»: تقسيم store.review + مستخدمو المتجر) ──
    # أُفرِد تأكيد الإيداع عن السحب فيَقدر المالك يَمنح أحدهما دون الآخر.
    # افتراضها OFF (مقيّد)؛ حارس store.review RBAC يَبقى فوقها (لا يُضعَف).
    "store.deposit_approve": {"label": "تأكيد الإيداع (المتجر)", "section": "store",
        "endpoints": ("store_support_deposit_confirm", "store_support_deposit_reject"),
        "default": False},
    "store.withdraw_approve": {"label": "تأكيد السحب (المتجر)", "section": "store",
        "endpoints": ("store_support_withdrawal_confirm", "store_support_withdrawal_reject"),
        "default": False},
    "storeuser.create": {"label": "إنشاء مستخدم متجر", "section": "store",
        "endpoints": ("card_users_create",), "default": False},
    "storeuser.edit": {"label": "تعديل مستخدم متجر (شحن/شراء)", "section": "store",
        "endpoints": ("card_user_recharge", "card_user_purchase"), "default": False},
    "storeuser.password": {"label": "تغيير كلمة مرور مستخدم متجر", "section": "store",
        "endpoints": ("card_user_password",), "default": False},
    # ── المرحلة D: أفعال خطرة ──
    # «العمليّات المجمّعة» بوّابة **إضافيّة** فوق فعل كل عمليّة (مسارات *_bulk
    # مربوطة سلفًا بأفعالها المفردة): virtual (بلا endpoints خاصّة)، يُنفَّذ عبر
    # BULK_ENDPOINTS في الحارس. افتراض OFF → المدير لا يُجري عمليّات جماعيّة
    # ما لم يَمنحها المالك.
    "bulk.ops": {"label": "العمليّات المجمّعة (تعديل/حذف جماعيّ)",
        "section": "subscribers", "endpoints": (), "default": False, "virtual": True},
}


# مسارات العمليّات المجمّعة — تُحرَس ببوّابة bulk.ops الإضافيّة (فوق فعلها المفرد).
BULK_ENDPOINTS: frozenset = frozenset({
    "users_bulk_delete", "users_toggle_bulk", "users_extend_bulk",
    "users_send_sms_bulk", "users_quota_topup_bulk", "users_quota_reset_daily_bulk",
    "users_balance_add_bulk", "users_payment_create_bulk", "users_loan_create_bulk",
    "cards_batches_bulk",
})


def bulk_blocked(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> bool:
    """هل endpoint عمليّةٌ مجمّعة والمدير غير مُصرَّح لها؟ (bulk.ops OFF)."""
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    if name not in BULK_ENDPOINTS:
        return False
    return not action_permitted(admin_id, "bulk.ops", tenant_id=tenant_id)


# عكس فهرس الأفعال: endpoint → مفتاح الفعل (ثابت، يُبنى عند الاستيراد).
_EP_TO_ACTION: dict[str, str] = {}
for _akey, _aspec in ACTION_REGISTRY.items():
    for _aep in _aspec.get("endpoints", ()):
        _EP_TO_ACTION.setdefault(_aep, _akey)


def action_names() -> tuple[str, ...]:
    return tuple(ACTION_REGISTRY.keys())


def endpoint_action(endpoint: str) -> Optional[str]:
    """مفتاح الفعل الذي يَخصّه endpoint (يَقبل radius.xxx أو xxx)، أو None."""
    if not endpoint:
        return None
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    return _EP_TO_ACTION.get(name)


def _action_overrides(admin_id: Optional[int], tenant_id: int) -> dict[str, bool]:
    """تجاوزات الأفعال المسطّحة (owner-off/on) — action_grants['_actions']."""
    ag = _grants_row(admin_id, tenant_id).get("action_grants") or {}
    flat = ag.get("_actions")
    return {k: bool(v) for k, v in flat.items()} if isinstance(flat, dict) else {}


def action_permitted(admin_id: Optional[int], action_key: str, *, tenant_id: int = 1) -> bool:
    """هل يُسمح للمدير بهذا الفعل؟ (السوبر يُعالَج قبل النداء في الحارس/الحاقن.)

      • فعل بعلَم can_*  → قيمة العلَم (افتراض OFF، مقيّد).
      • فعل «تعديل كيان» (offer/batch) → action_grants المتداخلة (افتراض OFF).
      • فعل يَحرسه RBAC   → تجاوز المالك الصريح إن وُجد، وإلّا الافتراض (True):
                            RBAC يَبقى الحاكم الفعليّ (غير انحداريّ)، والمالك
                            يَقدر يُطفئه صراحةً فيُصبح 403 مهما كان دور المدير."""
    spec = ACTION_REGISTRY.get(action_key)
    if not spec:
        return True
    flag = spec.get("flag")
    if flag:
        return bool(_grants_row(admin_id, tenant_id).get("flags", {}).get(flag))
    ent = spec.get("entity_edit")
    if ent:
        return action_allowed(admin_id, ent, "edit", tenant_id=tenant_id)
    ov = _action_overrides(admin_id, tenant_id).get(action_key)
    if ov is not None:
        return ov
    return bool(spec.get("default", True))


def endpoint_action_permitted(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> bool:
    """للحارس: هل endpoint (إن كان فعلًا مُسجَّلًا) مسموح للمدير؟ True إن لم
    يكن endpoint فعلًا مُسجَّلًا (لا قيد إضافيّ)."""
    akey = endpoint_action(endpoint)
    if not akey:
        return True
    return action_permitted(admin_id, akey, tenant_id=tenant_id)


def rbac_action_keys() -> tuple[str, ...]:
    """أفعال يَحرسها RBAC (بلا flag/entity_edit) — تُخزَّن تجاوزاتها المسطّحة."""
    return tuple(k for k, s in ACTION_REGISTRY.items()
                 if not s.get("flag") and not s.get("entity_edit"))


# ─── المرحلة A: السقوف الرقميّة (0 = بلا حدّ) — إنفاذ خادميّ بعدٍّ حيّ ───────
# مفاتيح السقوف في limits_json (DEFAULT_LIMITS). قابلة للتوسعة: أضِف مفتاحًا +
# نقطة إنفاذ. لا migration — نَعدّ من الجداول القائمة (subscribers/card_batches).
LIMIT_KEYS = ("max_subscribers", "max_cards_total", "max_cards_daily")


# ─── المرحلة B/C: صلاحيات الرؤية (server-side projection) — قابلة للتوسعة ────
# «رؤية» بيانات حسّاسة. الافتراض OFF (محجوب) — المالك يَمنحها. تُخزَّن كأعلام
# can_see_* في permissions_json وتُنفَّذ بحجب البيانات على الخادم (لا CSS).
# المرحلة B: الجملة/التكلفة. المرحلة C تُوسّعها (كلمة سر/رصيد/أرباح…).
VISIBILITY_REGISTRY: dict[str, str] = {
    "can_see_wholesale": "رؤية سعر التكلفة/الجملة",
    "can_see_password":  "رؤية كلمة مرور المشترك",
}


def can_see(admin_id: Optional[int], key: str, *, tenant_id: int = 1) -> bool:
    """هل يَملك المدير صلاحية رؤية بيانات حسّاسة؟ (الافتراض OFF/محجوب).
    السوبر/المالك يُعالَج في طبقة الحاقن/المُستدعي قبل النداء هنا."""
    return bool(_grants_row(admin_id, tenant_id).get("flags", {}).get(key))


def visibility_keys() -> tuple[str, ...]:
    return tuple(VISIBILITY_REGISTRY.keys())


def limit_value(admin_id: Optional[int], key: str, *, tenant_id: int = 1) -> int:
    """قيمة سقفٍ رقميّ للمدير (0/غياب = بلا حدّ)."""
    lims = _grants_row(admin_id, tenant_id).get("limits") or {}
    try:
        return max(0, int(lims.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def manager_subscriber_count(admin_id: int, *, tenant_id: int = 1) -> int:
    """عدد مشتركي المدير الحاليّين (غير المحذوفين)."""
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS n FROM subscribers "
            "WHERE tenant_id=? AND manager_id=? AND deleted_at IS NULL",
            (int(tenant_id or 1), int(admin_id)),
        ).fetchone()
        return int(row["n"] if row else 0)
    except Exception:  # noqa: BLE001 — لا نَكسر الإنشاء على خطأ عدّ
        return 0


def manager_card_count(admin_id: int, *, tenant_id: int = 1, today_only: bool = False) -> int:
    """مجموع بطاقات المدير (من card_batches.count). ``today_only`` = المُنشأة
    اليوم فقط (UTC) — للسقف اليوميّ."""
    try:
        from ..db.connection import db
        sql = ("SELECT COALESCE(SUM(count),0) AS n FROM card_batches "
               "WHERE tenant_id=? AND manager_id=?")
        params = [int(tenant_id or 1), int(admin_id)]
        if today_only:
            sql += " AND substr(COALESCE(created_at,''),1,10) = strftime('%Y-%m-%d','now')"
        row = db().execute(sql, params).fetchone()
        return int(row["n"] if row else 0)
    except Exception:  # noqa: BLE001
        return 0


def subscriber_cap_blocked(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """هل بلغ المدير سقف عدد المشتركين؟ (0 = بلا حدّ)."""
    cap = limit_value(admin_id, "max_subscribers", tenant_id=tenant_id)
    if cap <= 0 or not admin_id:
        return False
    return manager_subscriber_count(int(admin_id), tenant_id=tenant_id) >= cap


def card_cap_block_reason(admin_id: Optional[int], add_count: int, *, tenant_id: int = 1) -> Optional[str]:
    """يُرجع سبب المنع (عربيّ) إن كان توليد ``add_count`` بطاقة يَتجاوز السقف
    الإجماليّ أو اليوميّ — أو None إن كان مسموحًا. (0 = بلا حدّ.)"""
    if not admin_id or add_count <= 0:
        return None
    total_cap = limit_value(admin_id, "max_cards_total", tenant_id=tenant_id)
    daily_cap = limit_value(admin_id, "max_cards_daily", tenant_id=tenant_id)
    if total_cap > 0:
        cur = manager_card_count(int(admin_id), tenant_id=tenant_id)
        if cur + add_count > total_cap:
            return f"يتجاوز الحدّ الأقصى الإجماليّ للبطاقات ({total_cap})."
    if daily_cap > 0:
        cur_day = manager_card_count(int(admin_id), tenant_id=tenant_id, today_only=True)
        if cur_day + add_count > daily_cap:
            return f"يتجاوز الحدّ الأقصى اليوميّ للبطاقات ({daily_cap})."
    return None


def limits_catalog(admin_id: Optional[int], *, tenant_id: int = 1) -> list[dict[str, Any]]:
    """قائمة السقوف الرقميّة + قيمتها الحاليّة + الاستهلاك — لواجهة الإعداد."""
    lims = _grants_row(admin_id, tenant_id).get("limits") or {}
    def _v(k):
        try:
            return max(0, int(lims.get(k) or 0))
        except (TypeError, ValueError):
            return 0
    used_subs = manager_subscriber_count(int(admin_id), tenant_id=tenant_id) if admin_id else 0
    used_cards = manager_card_count(int(admin_id), tenant_id=tenant_id) if admin_id else 0
    return [
        {"key": "max_subscribers", "label": "أقصى عدد مشتركين", "value": _v("max_subscribers"), "used": used_subs},
        {"key": "max_cards_total", "label": "أقصى عدد بطاقات (إجماليّ)", "value": _v("max_cards_total"), "used": used_cards},
        {"key": "max_cards_daily", "label": "أقصى عدد بطاقات (يوميّ)", "value": _v("max_cards_daily"), "used": None},
    ]


# ─── المستوى 5: الإخفاء التلقائيّ للقسم «الفارغ» ──────────────────────────
# قسمٌ لا يَملك فيه المدير أيّ قدرة حقيقيّة (لا عرض، ولا فعل مُنِح، ولا حقل
# قابل للتعديل) يُخفى تلقائيًّا — سايدبار + 403 بالعنوان — حتى لو لم يَضبطه
# المالك «مخفي» صراحةً. «فارغ = مخفي فعليًّا».
_SECTION_ENTITIES: dict[str, tuple[str, ...]] = {
    "subscribers": ("subscriber",),
    "cards": ("offer", "batch"),
}


def _section_has_view(section: str, perms) -> bool:
    """هل يَستطيع المدير الوصول لأيّ endpoint عرضٍ في القسم؟ نُطابق منطق
    الشريط الجانبي ``section_can`` تمامًا (``can(perm_for_endpoint(ep))``):
      • endpoint بلا مفتاح صلاحية (مفتوح للجميع) → وصولٌ قائم = قدرة عرض.
      • endpoint بمفتاح يَملكه المدير → قدرة عرض.
    هكذا لا يُخفي «الفارغ» إلّا الأقسام المحروسة بالكامل التي لا يَملك المدير
    أيّ مفتاح فيها (مثل التقارير/المال) — فلا يَكسر مسارات المدير الافتراضيّة
    (استخدام العروض، قائمة المشتركين… مفتوحة)."""
    spec = MANAGER_SECTION_REGISTRY.get(section) or {}
    pset = set(perms or ())
    try:
        from ..auth.ui_permissions import perm_for_endpoint
    except Exception:  # noqa: BLE001
        vp = spec.get("view_perm")
        return bool(vp and vp in pset)
    for ep in spec.get("endpoints", ()):
        need = perm_for_endpoint(ep)
        if need is None:                      # مفتوح للجميع → وصولٌ قائم
            return True
        if need != "__super__" and need in pset:
            return True
    return False


def section_has_capability(admin_id: Optional[int], section: str, *, tenant_id: int = 1, perms=()) -> bool:
    """هل للمدير قدرة حقيقيّة واحدة على الأقل في القسم؟
      • عرض (RBAC) — أو
      • فعلٌ مُنِح صراحةً (علَم can_* أو «تعديل كيان») — أو
      • تحكّم حقليّ مُفعَّل بحقلٍ واحد على الأقل لكيان القسم.
    أفعال RBAC ذات الافتراض «مسموح» لا تُحسَب وحدها (تَعتمد على العرض/الدور)."""
    if _section_has_view(section, perms):
        return True
    for akey, aspec in ACTION_REGISTRY.items():
        if aspec.get("section") != section:
            continue
        if (aspec.get("flag") or aspec.get("entity_edit")) and \
                action_permitted(admin_id, akey, tenant_id=tenant_id):
            return True
    for entity in _SECTION_ENTITIES.get(section, ()):
        fg = field_grants(admin_id, entity, tenant_id=tenant_id)
        if fg:  # control on + ≥1 field
            return True
    return False


def effective_section_hidden(admin_id: Optional[int], section: str, *, tenant_id: int = 1, perms=()) -> bool:
    """الرؤية الفعليّة للقسم:
      • «مخفي» صراحةً → مخفيّ.
      • «مقفول» (عرض فقط) → ظاهر (العرض قدرة).
      • «مفتوح»/افتراضيّ → مخفيّ إن لم تكن للمدير أيّ قدرة (فارغ = مخفيّ)."""
    if section not in MANAGER_SECTION_REGISTRY:
        return False
    state = section_state(admin_id, section, tenant_id=tenant_id)
    if state == HIDDEN:
        return True
    if state == LOCKED:
        return False
    return not section_has_capability(admin_id, section, tenant_id=tenant_id, perms=perms)


def endpoint_effectively_hidden(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1, perms=()) -> bool:
    sec = section_of_endpoint(endpoint)
    if not sec:
        return False
    return effective_section_hidden(admin_id, sec, tenant_id=tenant_id, perms=perms)


def action_catalog(admin_id: Optional[int], *, tenant_id: int = 1) -> list[dict[str, Any]]:
    """مصفوفة الأفعال مجمّعة بالقسم — لواجهة الإعداد الموحّدة. كل عنصر يَحمل
    اسم مُدخَل النموذج الصحيح وحالته الحاليّة:
      • flag-backed  → input=can_* (يَحفظه parser الصلاحيات القائم)
      • entity_edit  → input=action_edit_<entity> (المرحلة 3)
      • rbac         → input=action_<key> (set_action_override؛ افتراضه True)"""
    by_section: dict[str, list[dict[str, Any]]] = {}
    for key, spec in ACTION_REGISTRY.items():
        if not spec.get("endpoints") and not spec.get("flag") and not spec.get("virtual"):
            continue  # فعل بلا مسار حقيقيّ ولا علَم ولا افتراضيّ (لا يُعرَض)
        flag = spec.get("flag")
        ent = spec.get("entity_edit")
        if flag:
            input_name, kind = flag, "flag"
        elif ent:
            input_name, kind = f"action_edit_{ent}", "entity_edit"
        else:
            input_name, kind = f"action_{key}", "rbac"
        by_section.setdefault(spec["section"], []).append({
            "key": key,
            "label": spec["label"],
            "input_name": input_name,
            "kind": kind,
            "checked": action_permitted(admin_id, key, tenant_id=tenant_id),
        })
    out: list[dict[str, Any]] = []
    for sec, spec in MANAGER_SECTION_REGISTRY.items():
        if sec in by_section:
            out.append({"section": sec, "label": spec.get("label", sec),
                        "icon": spec.get("icon", "folder"), "actions": by_section[sec]})
    return out


def set_action_override(admin_id: int, action_key: str, value: Optional[bool], *, tenant_id: int = 1) -> None:
    """يَضبط تجاوز فعلٍ يَحرسه RBAC (True/False)، أو يَحذفه (None=للافتراض)."""
    ag = dict(_grants_row(admin_id, tenant_id).get("action_grants") or {})
    flat = dict(ag.get("_actions") or {})
    if value is None:
        flat.pop(action_key, None)
    else:
        flat[action_key] = bool(value)
    ag["_actions"] = flat
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "action_grants_json", ag)
    _invalidate_cache()


def entity_field_defs(entity: str) -> tuple[dict[str, Any], ...]:
    """قائمة تعريفات الحقول القابلة للمنح لكيان (لعرض قائمة الإعداد)."""
    return FIELD_REGISTRY.get(entity, ())


def field_keys(entity: str) -> tuple[str, ...]:
    return tuple(f["key"] for f in FIELD_REGISTRY.get(entity, ()))


# عكس الفهرس: endpoint → اسم القسم (ثابت، يُبنى عند الاستيراد). endpoint في
# أكثر من قسم يُحسم لصالح أوّل قسم يُدرِجه (ترتيب السجلّ).
_EP_TO_SECTION: dict[str, str] = {}
for _sec, _spec in MANAGER_SECTION_REGISTRY.items():
    for _ep in _spec["endpoints"]:
        _EP_TO_SECTION.setdefault(_ep, _sec)


def section_names() -> tuple[str, ...]:
    return tuple(MANAGER_SECTION_REGISTRY.keys())


def section_of_endpoint(endpoint: str) -> Optional[str]:
    """اسم القسم الذي ينتمي إليه endpoint (يَقبل ``radius.xxx`` أو ``xxx``)."""
    if not endpoint:
        return None
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    return _EP_TO_SECTION.get(name)


def is_mutating_method(method: str) -> bool:
    """هل الطلب كتابة؟ (locked يَسمح بالعرض ويَحجب الكتابة). GET/HEAD/OPTIONS
    = عرض؛ أيّ شيء آخر (POST/PUT/PATCH/DELETE) = كتابة."""
    return (method or "GET").upper() not in ("GET", "HEAD", "OPTIONS")


# ─── قراءة/تخزين grants صفّ المدير ────────────────────────────────────────
def _load(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _grants_row(admin_id: Optional[int], tenant_id: int) -> dict[str, Any]:
    """يُرجع {section_access, action_grants, field_grants} للمدير — من
    ``manager_distributor_policies`` (entity_type='manager'). مخزَّن لكل طلب.

    لا يُنشئ صفًّا (قراءة فقط، الافتراض الآمن = فارغ). محصّن: أيّ خطأ DB
    يُرجع فارغًا (fail-open للأقسام: غياب سياسة = مفتوح، غير انحداريّ)."""
    empty = {"section_access": {}, "action_grants": {}, "field_grants": {},
             "flags": {}, "limits": {}}
    if not admin_id:
        return empty
    key = (int(tenant_id or 1), int(admin_id))
    cache = getattr(g, "_mg_grants_cache", None)
    if isinstance(cache, dict) and cache.get("_key") == key:
        return cache["val"]
    val = dict(empty)
    try:
        from ..db.connection import db
        row = db().execute(
            """
            SELECT section_access_json, action_grants_json, field_grants_json,
                   permissions_json, limits_json
            FROM manager_distributor_policies
            WHERE tenant_id=? AND entity_type='manager' AND entity_id=?
            """,
            (key[0], key[1]),
        ).fetchone()
        if row:
            val = {
                "section_access": _load(row["section_access_json"]),
                "action_grants": _load(row["action_grants_json"]),
                "field_grants": _load(row["field_grants_json"]),
                "flags": _load(row["permissions_json"]),
                "limits": _load(row["limits_json"]),
            }
    except Exception:  # noqa: BLE001 — fail-open: لا نَكسر أيّ طلب على خطأ DB
        val = dict(empty)
    try:
        g._mg_grants_cache = {"_key": key, "val": val}
    except Exception:  # noqa: BLE001 — خارج سياق الطلب (اختبارات/CLI)
        pass
    return val


def _invalidate_cache() -> None:
    try:
        if hasattr(g, "_mg_grants_cache"):
            delattr(g, "_mg_grants_cache")
    except Exception:  # noqa: BLE001
        pass


# ─── المستوى 1: حالة القسم ────────────────────────────────────────────────
def section_state(admin_id: Optional[int], section: str, *, tenant_id: int = 1) -> str:
    """حالة قسمٍ للمدير: open/locked/hidden. غير المُهيّأ = DEFAULT_SECTION_STATE."""
    if section not in MANAGER_SECTION_REGISTRY:
        return OPEN
    access = _grants_row(admin_id, tenant_id).get("section_access") or {}
    val = str(access.get(section) or "").strip().lower()
    return val if val in SECTION_STATES else DEFAULT_SECTION_STATE


def endpoint_state(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> str:
    """حالة القسم الذي يَخصّه endpoint (open إن لم يَنتمِ لأيّ قسم مُدار)."""
    sec = section_of_endpoint(endpoint)
    if not sec:
        return OPEN
    return section_state(admin_id, sec, tenant_id=tenant_id)


def is_endpoint_hidden_for(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> bool:
    return endpoint_state(admin_id, endpoint, tenant_id=tenant_id) == HIDDEN


def is_endpoint_locked_for(admin_id: Optional[int], endpoint: str, *, tenant_id: int = 1) -> bool:
    return endpoint_state(admin_id, endpoint, tenant_id=tenant_id) == LOCKED


def get_section_access(admin_id: Optional[int], *, tenant_id: int = 1) -> dict[str, str]:
    """الخريطة الكاملة (كل قسم → حالته الحاليّة) لواجهة الإعداد."""
    access = _grants_row(admin_id, tenant_id).get("section_access") or {}
    out: dict[str, str] = {}
    for name in MANAGER_SECTION_REGISTRY:
        val = str(access.get(name) or "").strip().lower()
        out[name] = val if val in SECTION_STATES else DEFAULT_SECTION_STATE
    return out


def section_catalog(admin_id: Optional[int], *, tenant_id: int = 1) -> list[dict[str, Any]]:
    """قائمة الأقسام + حالتها الحاليّة — لعرض مصفوفة الإعداد في القالب."""
    states = get_section_access(admin_id, tenant_id=tenant_id)
    return [
        {
            "name": name,
            "label": spec.get("label", name),
            "icon": spec.get("icon", "folder"),
            "state": states.get(name, DEFAULT_SECTION_STATE),
        }
        for name, spec in MANAGER_SECTION_REGISTRY.items()
    ]


# ─── الكتابة (من صفحة الصلاحيات) ─────────────────────────────────────────
def _ensure_policy_row(admin_id: int, tenant_id: int) -> None:
    """يَضمن وجود صفّ سياسة للمدير قبل UPDATE عمود grants — دون المساس بأيّ
    عمود آخر. نَستخدم ``get_policy(create=True)`` الذي يُنشئ الصفّ بالافتراضات
    **فقط إن كان غائبًا** (لا يُعيد كتابة الصلاحيات/الحدود القائمة — بخلاف
    ``set_policy`` الذي كان يَمسح permissions_json عند غياب المعامل)."""
    from .manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=int(tenant_id or 1)).get_policy(
        entity_type="manager", entity_id=int(admin_id), create=True)


def set_section_access(
    admin_id: int, mapping: dict[str, str], *, tenant_id: int = 1, by: int = 0
) -> dict[str, str]:
    """يَحفظ خريطة حالة الأقسام للمدير (يُطبّع القيم؛ يَتجاهل المفاتيح المجهولة).

    يُنشئ صفّ السياسة إن لم يكن موجودًا (عبر ``set_policy`` الذي يَبذر
    الافتراضات) ثم يُحدِّث عمود ``section_access_json`` وحده — دون المساس
    بالصلاحيات/الحدود الأخرى على الصفّ."""
    clean: dict[str, str] = {}
    for name, val in (mapping or {}).items():
        if name not in MANAGER_SECTION_REGISTRY:
            continue
        v = str(val or "").strip().lower()
        if v in SECTION_STATES and v != DEFAULT_SECTION_STATE:
            # نُخزّن فقط ما يَنحرف عن الافتراضي (open) — يُبقي الصفّ نظيفًا
            # وقابلية القراءة العكسيّة سليمة (الغياب = open).
            clean[name] = v
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "section_access_json", clean)
    _invalidate_cache()
    return clean


def _write_column(admin_id: int, tenant_id: int, column: str, value: dict[str, Any]) -> None:
    """كتابة عمود grants واحد على صفّ سياسة المدير (JSON)."""
    if column not in ("section_access_json", "action_grants_json", "field_grants_json"):
        raise ValueError(f"unknown grants column: {column}")
    from ..db.connection import db
    from ..db.helpers import now_iso
    db().execute(
        f"""
        UPDATE manager_distributor_policies
        SET {column}=?, updated_at=?
        WHERE tenant_id=? AND entity_type='manager' AND entity_id=?
        """,
        (json.dumps(value or {}, ensure_ascii=False, sort_keys=True), now_iso(),
         int(tenant_id or 1), int(admin_id)),
    )


# ─── المستوى 2 و3: بوّابات الفعل والحقل (تخزين + قراءة؛ الإنفاذ في المراحل
#      التالية عبر مستهلكين في users_update / offer / batch update) ────────
def action_grants(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> Optional[dict[str, bool]]:
    """بوّابات create/edit/delete لكيان — أو None إن لم تُهيّأ (تحكّم مطفأ)."""
    grants = _grants_row(admin_id, tenant_id).get("action_grants") or {}
    ent = grants.get(entity)
    if not isinstance(ent, dict):
        return None
    return {k: bool(v) for k, v in ent.items()}


def field_grants(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> Optional[set[str]]:
    """الحقول المسموح للمدير تعديلها في كيان.

    - ``None`` = التحكّم الحقليّ **مطفأ** لهذا الكيان (كل الحقول قابلة للتعديل،
      سلوك اليوم — غير انحداريّ).
    - مجموعة (قد تكون فارغة) = التحكّم **مُفعَّل**: فقط هذه الحقول قابلة
      للتعديل؛ ما عداها يُسقَط/يُرفَض خادميًّا."""
    grants = _grants_row(admin_id, tenant_id).get("field_grants") or {}
    if entity not in grants:
        return None
    val = grants.get(entity)
    if not isinstance(val, list):
        return None
    return {str(x) for x in val}


def field_control_on(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> bool:
    """هل التحكّم الحقليّ مُفعَّل لهذا الكيان للمدير؟ (مفتاح الكيان موجود)."""
    return field_grants(admin_id, entity, tenant_id=tenant_id) is not None


def field_locked(admin_id: Optional[int], entity: str, key: str, *, tenant_id: int = 1) -> bool:
    """هل حقلٌ (بمفتاحه) مقفولٌ على المدير؟ = التحكّم مُفعَّل والحقل غير ممنوح.
    (السوبر يُعالَج في طبقة الحاقن قبل الوصول هنا.)"""
    granted = field_grants(admin_id, entity, tenant_id=tenant_id)
    if granted is None:
        return False
    return key not in granted


def reverted_attrs(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> set[str]:
    """أسماء حقول الـDTO التي يجب إعادتها لقيمتها القائمة (غير ممنوحة).

    - التحكّم مطفأ (لا مفتاح كيان) → مجموعة فارغة (لا إعادة، سلوك اليوم).
    - مُفعَّل → اتحاد ``attrs`` لكل حقلٍ مفتاحُه **غير** ضمن الممنوح."""
    granted = field_grants(admin_id, entity, tenant_id=tenant_id)
    if granted is None:
        return set()
    out: set[str] = set()
    for fdef in FIELD_REGISTRY.get(entity, ()):
        if fdef["key"] not in granted:
            out.update(fdef["attrs"])
    return out


def action_allowed(admin_id: Optional[int], entity: str, action: str, *, tenant_id: int = 1) -> bool:
    """هل مُنِح المدير فعلًا (create/edit/delete) على كيان؟ الافتراض الآمن =
    False (لم يُمنَح) — يُبقي عقد «مالك فقط» القائم لعرض/حزمة البطاقات ما لم
    يَفتحه المالك صراحةً (opt-in، غير انحداريّ). السوبر يُعالَج قبل النداء."""
    grants = action_grants(admin_id, entity, tenant_id=tenant_id)
    return bool(grants and grants.get(action))


def drop_ungranted_keys(admin_id: Optional[int], entity: str, data: dict, *, tenant_id: int = 1) -> dict:
    """يُزيل من ``data`` مفاتيحَ الحقول غير الممنوحة (dict-based، لـupdate_batch:
    المفاتيح غير المُدرَجة لا تُحدَّث فتَبقى كما هي). لا تغيير إن كان التحكّم
    مطفأً."""
    reverts = reverted_attrs(admin_id, entity, tenant_id=tenant_id)
    if not reverts:
        return dict(data)
    return {k: v for k, v in data.items() if k not in reverts}


def enforce_dto(admin_id: Optional[int], entity: str, incoming, existing, *, tenant_id: int = 1):
    """يُعيد نسخةً من الـDTO الواردة بعد إعادة الحقول غير الممنوحة إلى قيَم
    ``existing`` (المشترك قبل الحفظ). لا يُغيّر شيئًا إن كان التحكّم مطفأً أو
    ``existing`` غائبًا. يُستخدَم في معالِج التحديث الخادميّ — الدفاع الحقيقيّ
    (لا يُوثَق بالعميل: أيّ POST مُلفَّق لحقلٍ غير ممنوح يُتجاهَل)."""
    if existing is None:
        return incoming
    reverts = reverted_attrs(admin_id, entity, tenant_id=tenant_id)
    if not reverts:
        return incoming
    from dataclasses import replace
    patch = {a: getattr(existing, a) for a in reverts if hasattr(existing, a) and hasattr(incoming, a)}
    if not patch:
        return incoming
    return replace(incoming, **patch)


def set_field_grants(
    admin_id: int, entity: str, fields: Optional[Iterable[str]], *, tenant_id: int = 1
) -> None:
    """يَضبط الحقول المسموحة لكيان. ``fields=None`` يُطفئ التحكّم (يَحذف المفتاح)."""
    current = dict(_grants_row(admin_id, tenant_id).get("field_grants") or {})
    if fields is None:
        current.pop(entity, None)
    else:
        current[entity] = sorted({str(f) for f in fields})
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "field_grants_json", current)
    _invalidate_cache()


def set_action_grants(
    admin_id: int, entity: str, actions: Optional[dict[str, bool]], *, tenant_id: int = 1
) -> None:
    """يَضبط بوّابات create/edit/delete لكيان. ``actions=None`` يُطفئ التحكّم."""
    current = dict(_grants_row(admin_id, tenant_id).get("action_grants") or {})
    if actions is None:
        current.pop(entity, None)
    else:
        current[entity] = {k: bool(v) for k, v in actions.items()}
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "action_grants_json", current)
    _invalidate_cache()


__all__ = [
    "OPEN", "LOCKED", "HIDDEN", "SECTION_STATES", "DEFAULT_SECTION_STATE",
    "MANAGER_SECTION_REGISTRY", "section_names", "section_of_endpoint",
    "is_mutating_method", "section_state", "endpoint_state",
    "is_endpoint_hidden_for", "is_endpoint_locked_for",
    "get_section_access", "section_catalog", "set_section_access",
    "action_grants", "field_grants", "set_field_grants", "set_action_grants",
    "FIELD_REGISTRY", "entity_field_defs", "field_keys", "field_control_on",
    "field_locked", "reverted_attrs", "enforce_dto",
    "action_allowed", "drop_ungranted_keys",
    "ACTION_REGISTRY", "action_names", "endpoint_action", "action_permitted",
    "endpoint_action_permitted", "set_action_override", "rbac_action_keys",
    "action_catalog", "section_has_capability", "effective_section_hidden",
    "endpoint_effectively_hidden",
    "LIMIT_KEYS", "limit_value", "manager_subscriber_count", "manager_card_count",
    "subscriber_cap_blocked", "card_cap_block_reason", "limits_catalog",
    "VISIBILITY_REGISTRY", "can_see", "visibility_keys",
    "BULK_ENDPOINTS", "bulk_blocked",
]
