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
            "plans_create", "plans_clone", "plans_update", "plans_delete",
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
            # fix3 (D15): WhatsApp SETTINGS are gated by settings.edit alone —
            # an empty «communications» section no longer hides them.
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
        # D21: إعادة تسمية اسم الدخول قابلة للمنح تحت التحكّم الحقليّ.
        {"key": "username", "label": "اسم الدخول (إعادة تسمية)", "attrs": ("username",)},
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
    # يشمل نموذج «إضافة مشترك» (GET users_new) مع gate_get: كي يُحرَس فتح
    # النموذج بنفس منحة الحفظ، فلا «يفتح ثم يُرفَض» — الطريق المسدود. المدير بلا
    # منحة «إنشاء مشترك» لا يفتح النموذج أصلًا (والزرّ مخفيّ عبر manager_action_allowed).
    "subscriber.create": {"label": "إنشاء مشترك", "section": "subscribers",
        "endpoints": ("users_create", "users_new"), "flag": "can_create_subscriber",
        "gate_get": True},
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
    # ── مايكروتيك: صفحات إدارة الراوتر (تنبيهات ذكية، تدقيق، نسخ احتياطي،
    #    مصمّم الدخول، أدوات mt). «مدير عام» يفتحها افتراضًا؛ هذا المفتاح
    #    يتيح للمالك منعها عن مدير بعينه («ممنوع») أو عن الدور. الحرّاس
    #    القديمة (mt_permissions.requires_perm) تبقى فوقه. default=True.
    # 🔴 `virtual: True` **إلزاميّ** هنا: المفتاحُ بلا `endpoints` (الحراسةُ عبر
    #    mt_permissions لا عبر جدولِ نقاطِ النهاية)، وبناءُ المصفوفةِ يتخطّى كلَّ
    #    فعلٍ بلا endpoints/flag/virtual ⇒ لا مربّعَ يُرسم. وبما أنّ الحفظَ يقرأ
    #    `action_mikrotik.access` من النموذج، كان غيابُ المربّعِ يُقرأ «ممنوع»
    #    فيكتبُ **كلُّ حفظٍ لأساسِ الدور — حتى بلا أيِّ تغيير — تجاوزًا لم يطلبه
    #    المالك** (‏NEW-1، نفسُ نمطِ D01 الذي كلّف الحملةَ جولتَين). والعلَمُ
    #    يُظهرُ المفتاحَ للمالكِ أيضًا، وهو ما طلبه صريحًا (‏NEW-2).
    "mikrotik.access": {"label": "الوصول لصفحات مايكروتيك", "section": "sessions",
        "endpoints": (), "default": True, "virtual": True},
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
        # D26: إلغاء السرعة المؤقتة من ملف المشترك = نفس البوّابة (users.temp_speed)
        # كشاشة المتصلين — لا صلاحيةٌ في صفحة وأخرى في غيرها.
        "endpoints": ("online_temp_speed", "online_temp_speed_cancel",
                      "users_temp_speed_cancel"), "default": False},
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
    # إضافةُ عرضٍ كانت مقصورةً على المالك بشرطٍ مثبَّتٍ في المسار، فلا تُمنَح
    # مهما فعل المالك. صارت بوّابةَ فعلٍ على الكيان نفسه (offer) بعمليّة
    # ``create`` — **افتراضُها OFF** فلا يتغيّر سلوك أيّ نسخةٍ قائمة، والمالك
    # يَفتحها لمن يشاء من صفحة صلاحيّات المدير.
    "offer.create": {"label": "إضافة عرض", "section": "cards",
        "endpoints": ("cards_offer_create",), "entity_edit": "offer",
        "entity_op": "create"},
    # ── الباقات ──
    "plan.create": {"label": "إنشاء باقة", "section": "plans",
        "endpoints": ("plans_create", "plans_clone"), "default": True},
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
        "endpoints": ("export_table", "users_export", "cards_batches_export_csv",
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
    "storeuser.delete": {"label": "حذف/استعادة مستخدم متجر", "section": "store",
        "endpoints": ("card_user_delete", "card_user_restore"), "default": False},
    # ── المرحلة D: أفعال خطرة ──
    # «العمليّات المجمّعة» بوّابة **إضافيّة** فوق فعل كل عمليّة (مسارات *_bulk
    # مربوطة سلفًا بأفعالها المفردة): virtual (بلا endpoints خاصّة)، يُنفَّذ عبر
    # BULK_ENDPOINTS في الحارس. افتراض OFF → المدير لا يُجري عمليّات جماعيّة
    # ما لم يَمنحها المالك.
    "bulk.ops": {"label": "العمليّات المجمّعة (تعديل/حذف جماعيّ)",
        "section": "subscribers", "endpoints": (), "default": False, "virtual": True},
}

# ── التوحيد: «الصلاحيات» (RBAC) هي المصدر الوحيد ──────────────────────────────
# الأفعال التي تُكرّر صلاحيةً في مصفوفة RBAC تُشتقّ منها مباشرةً: منح الصلاحية =
# القدرة تعمل، بلا منحة فعلٍ منفصلة. هذا يُزيل التكرار في محرّر الأدوار ويمنع
# «القفل» و«الطريق المسدود» (منح RBAC لكن الفعل مُطفأ). المالك يقدر يُطفئ فعلًا
# مُشتقًّا صراحةً عبر _actions override فيُصبح 403 مهما كانت الصلاحية. الأفعال
# التي لا مقابل RBAC لها (أيام مجانية/تجريبية، الاتصالات، الموزّعون، تعديل
# العرض/الحزمة، مزامنة/تعديل الجلسة، العمليّات المجمّعة، المتجر) تبقى منحًا
# مستقلّة تُعرَض في المحرّر.
_ACTION_RBAC_PERM: dict[str, str] = {
    "subscriber.create":          "users.create",
    "subscriber.delete":          "users.delete",
    "subscriber.status":          "users.change_status",
    "subscriber.extend":          "users.extend",
    "subscriber.renew":           "users.change_plan",
    "subscriber.quota":           "users.quota",
    "subscriber.balance_add":     "users.balance_add",
    "subscriber.payment":         "users.payments",
    "subscriber.loan":            "users.loans",
    "subscriber.send_credentials": "users.send_message",
    # ملاحظة: أفعال «الجلسات» (disconnect/lock_mac/lock_ip/temp_speed) تُركت
    # منحًا دقيقة مستقلّة عمدًا (نظام «وسّع المجال») — ليست ضمن التكرار الذي
    # اشتكى منه المالك (المستفيد/البطاقات/الباقات)، فلا نمسّها.
    "cards.generate":             "cards.generate",
    "cards.import":               "cards.import",
    "cards.revoke":               "cards.revoke",
    "cards.batch_ops":            "cards.batch_ops",
    "cards.recharge":             "cards.recharge",
    "cards.print":                "cards.print",
    "plan.create":                "plans.create",
    "plan.edit":                  "plans.edit",
    "plan.delete":                "plans.delete",
    "data.export":                "users.export",
    # ── D15 (fix wave 2): بوّابات مخفيّة افتراضها OFF كانت تُرجع 403 رغم منح
    # صلاحية RBAC المطابقة («منحتُ قطع الاتصال ولم يعمل»). تُشتقّ الآن من مفتاح
    # RBAC نفسه — مفتاحٌ واحد في محرّر الأدوار. tuple = يكفي أحدها؛ دقّة كل
    # endpoint يحرسها _PERM_GUARDED (مثلًا online_coa_set_ip ← online.lock_ip). ──
    "session.disconnect":         "online.disconnect",
    "session.force_close":        "online.disconnect",
    "session.reconcile":          "online.disconnect",
    "session.lock_mac":           "online.lock_mac",
    "session.lock_ip":            "online.lock_ip",
    "session.edit":               ("online.lock_ip", "users.temp_speed"),
    "session.temp_speed":         "users.temp_speed",
    "comms.sms":                  "users.send_message",
    "comms.templates":            "users.send_message",
    "comms.whatsapp":             "settings.edit",
    "store.deposit_approve":      "store.review",
    "store.withdraw_approve":     "store.review",
    "storeuser.create":           "store.user_add",
    "storeuser.edit":             ("store.user_recharge", "store.user_purchase"),
    "storeuser.password":         "store.user_edit",
    "storeuser.delete":           "store.user_delete",
    "batch.edit":                 "cards.edit_batch",
}
for _ak, _rp in _ACTION_RBAC_PERM.items():
    if _ak in ACTION_REGISTRY:
        ACTION_REGISTRY[_ak]["rbac_perm"] = _rp


def _rbac_any(rbac, perms) -> bool:
    """مفتاح RBAC مفرد أو tuple (يكفي أحدها)."""
    if isinstance(rbac, (tuple, list, set, frozenset)):
        return any(p in perms for p in rbac)
    return rbac in perms


def rbac_perm_label(action_key: str) -> str:
    """مفاتيح RBAC المطلوبة لفعلٍ مُشتقّ، نصًّا (لرسائل الرفض)."""
    rbac = (ACTION_REGISTRY.get(action_key) or {}).get("rbac_perm")
    if isinstance(rbac, (tuple, list)):
        return " أو ".join(rbac)
    return str(rbac or "")


def is_derived_action(action_key: str) -> bool:
    """فعلٌ مُشتقّ من صلاحية RBAC (لا مربّع له في المحرّر؛ مفتاح الدور هو التحكّم)."""
    return bool((ACTION_REGISTRY.get(action_key) or {}).get("rbac_perm"))


def derived_action_keys() -> tuple[str, ...]:
    return tuple(k for k, s in ACTION_REGISTRY.items() if s.get("rbac_perm"))


# أعلام can_* تُبقي مفتاح التخزين لأفعالٍ صارت مُشتقّة من RBAC — قراءتها
# (has_permission) تمرّ على action_permitted كي لا يبقى مصدران.
FLAG_DERIVED_ACTION: dict[str, str] = {
    spec["flag"]: key for key, spec in ACTION_REGISTRY.items()
    if spec.get("flag") and spec.get("rbac_perm")
}


def _admin_rbac_perms(admin_id: Optional[int], tenant_id: int) -> frozenset[str]:
    """صلاحيات RBAC الفعليّة للمدير — للمدير الحالي نستخدم صلاحيات الجلسة (هي
    صلاحيات دوره المحمَّلة عند الدخول، وهي الحاكمة لطلبه)؛ ولغيره نحمّلها من دوره.
    مخبّأة لكل طلب عبر flask.g."""
    if not admin_id:
        return frozenset()
    # المدير الحالي في الطلب: صلاحيات الجلسة هي المصدر (تطابق حرّاس RBAC القائمة).
    try:
        from flask import session as _session
        if _session.get("admin_id") == admin_id and "permissions" in _session:
            return frozenset(_session.get("permissions") or ())
    except Exception:  # noqa: BLE001 — لا سياق طلب
        pass
    try:
        from flask import g as _g
        cache = getattr(_g, "_rbac_perms_cache", None)
        if cache is None:
            cache = {}
            _g._rbac_perms_cache = cache
        if admin_id in cache:
            return cache[admin_id]
    except Exception:  # noqa: BLE001 — لا سياق طلب (اختبار/عامل)
        cache = None
    perms: frozenset[str] = frozenset()
    try:
        from ..db.repos import admins_repo
        from .admins import get_admins_service
        admin = admins_repo.get_admin(int(admin_id))
        if admin is not None:
            perms = frozenset(get_admins_service().permissions_of(admin))
    except Exception:  # noqa: BLE001 — تعذّر الحلّ = لا صلاحيّات مشتقّة
        perms = frozenset()
    if cache is not None:
        cache[admin_id] = perms
    return perms


# مسارات العمليّات المجمّعة — تُحرَس ببوّابة bulk.ops الإضافيّة (فوق فعلها المفرد).
BULK_ENDPOINTS: frozenset = frozenset({
    "users_bulk_delete", "users_toggle_bulk", "users_extend_bulk",
    "users_send_sms_bulk", "users_quota_topup_bulk", "users_quota_reset_daily_bulk",
    "users_balance_add_bulk", "users_payment_create_bulk", "users_loan_create_bulk",
    # fix3 (D15 / F01 F14): cards_batches_bulk is NOT here — batch operations
    # ARE the «cards.batch_ops» key (its own action gate); a hidden per-manager
    # bulk.ops grant made the key unusable. Purge stays owner-only in-handler.
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
    # ── التوحيد: فعلٌ مُشتقّ من صلاحية RBAC → مصدره الصلاحية وحدها ──
    rbac = spec.get("rbac_perm")
    if rbac:
        # تعليق المدير المؤقّت (انتهاء المنح) يُعلّق أفعاله المشتقّة أيضًا.
        if grants_expired(admin_id, tenant_id=tenant_id):
            return False
        ov = _action_overrides(admin_id, tenant_id).get(action_key)
        if ov is False:                       # إطفاء صريح (تفويض فرعيّ) يَبقى حاكمًا
            return False
        if _rbac_any(rbac, _admin_rbac_perms(admin_id, tenant_id)):
            # سقف التفويض وقت التشغيل: المدير الفرعيّ يرث دور أبيه، فلا يصل
            # فعلًا مُشتقًّا أطفأه المالكُ لأبيه (الابن ≤ الأب دائمًا).
            return _parent_allows(admin_id, action_key, tenant_id)
        # «تعديل الحزمة»: المنحة الصريحة القديمة على الكيان تبقى صالحة (توافق).
        ent = spec.get("entity_edit")
        return bool(ent and action_allowed(admin_id, ent, spec.get("entity_op", "edit"),
                                           tenant_id=tenant_id))
    flag = spec.get("flag")
    if flag:
        return bool(_grants_row(admin_id, tenant_id).get("flags", {}).get(flag))
    ent = spec.get("entity_edit")
    if ent:
        # ``entity_op`` يسمح لكيانٍ واحدٍ بأكثر من بوّابة (تعديل/إضافة).
        # غيابُه = "edit" كما كان، فلا ينكسر أيُّ مفتاحٍ قائم.
        return action_allowed(admin_id, ent, spec.get("entity_op", "edit"),
                              tenant_id=tenant_id)
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
    """أفعالٌ لها مربّع «action_<key>» في المحرّر وتُخزَّن تجاوزاتها المسطّحة
    (بلا flag/entity_edit). **تستثني الأفعال المُشتقّة من RBAC** (D01): لا مربّع
    لها، فكان كلُّ حفظٍ لصفحة المدير/أساس الدور يقرأ غيابها «مُطفأ» ويخزّن
    False صريحًا يغلب صلاحية الدور — 14 فعلًا تنطفئ بصمت عند كل حفظ."""
    return tuple(k for k, s in ACTION_REGISTRY.items()
                 if not s.get("flag") and not s.get("entity_edit")
                 and not s.get("rbac_perm"))


def editable_flag_keys() -> tuple[str, ...]:
    """أعلام can_* التي لها مربّعٌ في صفحة المدير/محرّر الدور: أعلام نطاق الرؤية
    + أعلام الأفعال غير المُشتقّة. أعلام الأفعال المُشتقّة (إنشاء/تفعيل/سلفة/
    توليد/استيراد) لا تُحفظ من النموذج — مصدرها صلاحية RBAC."""
    out = list(SCOPE_FLAG_REGISTRY.keys())
    for _k, spec in ACTION_REGISTRY.items():
        f = spec.get("flag")
        if f and not spec.get("rbac_perm") and f not in out:
            out.append(f)
    return tuple(out)


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
    "can_see_balance":   "رؤية الرصيد والماليّات",
    "can_see_profit":    "رؤية الأرباح/الهامش",
}


def can_see(admin_id: Optional[int], key: str, *, tenant_id: int = 1) -> bool:
    """هل يَملك المدير صلاحية رؤية بيانات حسّاسة؟ (الافتراض OFF/محجوب).
    السوبر/المالك يُعالَج في طبقة الحاقن/المُستدعي قبل النداء هنا."""
    return bool(_grants_row(admin_id, tenant_id).get("flags", {}).get(key))


def visibility_keys() -> tuple[str, ...]:
    return tuple(VISIBILITY_REGISTRY.keys())


# ─── F3: المدراء الفرعيّون + سقف التفويض ──────────────────────────────────
def parent_admin_id(admin_id: Optional[int]) -> Optional[int]:
    """معرّف المدير الأب (parent) لهذا المدير — أو None (لا أب)."""
    if not admin_id:
        return None
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT parent_admin_id FROM admins WHERE id=?", (int(admin_id),)).fetchone()
        return int(row["parent_admin_id"]) if row and row["parent_admin_id"] else None
    except Exception:  # noqa: BLE001
        return None


def can_create_sub_managers(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """هل يَملك المدير صلاحية إنشاء مدراء فرعيّين؟ (علَم، افتراض OFF)."""
    return bool(_grants_row(admin_id, tenant_id).get("flags", {}).get("can_create_sub_managers"))


def parent_has_grant(parent_id: Optional[int], kind: str, key: str, *, tenant_id: int = 1) -> bool:
    """هل يَملك الأب هذا المنح؟ (سقف التفويض: الابن لا يَحصل ما لا يَملكه الأب.)
    ``kind`` = "flag" (علَم can_*) أو "action" (مفتاح فعل)."""
    if not parent_id:
        return False
    if kind == "flag":
        return bool(_grants_row(parent_id, tenant_id).get("flags", {}).get(key))
    if kind == "action":
        return action_permitted(parent_id, key, tenant_id=tenant_id)
    return False


def _parent_allows(admin_id: Optional[int], action_key: str, tenant_id: int,
                   _depth: int = 0) -> bool:
    """هل يسمح أبُ المدير الفرعيّ (وسلسلة آبائه) بهذا الفعل المُشتقّ؟ لا أب/أبٌ
    بمقام المالك → True. حدّ عمقٍ يمنع الحلقات."""
    pid = parent_admin_id(admin_id)
    if not pid or int(pid) == int(admin_id or 0) or _depth > 5:
        return True
    try:
        from ..auth.owner import is_owner_like
        if is_owner_like(int(pid)):
            return True
    except Exception:  # noqa: BLE001
        pass
    spec = ACTION_REGISTRY.get(action_key) or {}
    if _action_overrides(pid, tenant_id).get(action_key) is False:
        return False
    if not _rbac_any(spec.get("rbac_perm"), _admin_rbac_perms(pid, tenant_id)):
        return False
    return _parent_allows(pid, action_key, tenant_id, _depth + 1)


# ─── fix wave 2: سقف التفويض للحدود والائتمان (الابن ≤ الأب) ─────────────
#: الحدود الرقميّة التي يفوّضها الأب لابنه (0/فارغ = بلا حدّ).
DELEGABLE_LIMIT_KEYS: tuple[str, ...] = (
    "max_free_days", "max_trial_days", "max_subscribers", "max_cards_total",
    "max_cards_daily", "spend_cap_daily", "spend_cap_monthly",
)


def _num(v: Any) -> float:
    try:
        from ..core.numbers import normalize_number_text
        return max(0.0, float(normalize_number_text(str(v if v is not None else "")) or 0))
    except Exception:  # noqa: BLE001 — قيمة معطوبة = بلا حدّ
        return 0.0


def clamp_to_parent_cap(requested: Any, parent_cap: Any) -> float:
    """قيمةٌ يطلبها الأب لابنه مقصوصةٌ على سقف الأب (0 = بلا حدّ):
    أبٌ بلا حدّ → كما طُلب؛ أبٌ بحدّ P → «بلا حدّ» تصير P، وما فوق P يصير P."""
    p, c = _num(parent_cap), _num(requested)
    if p <= 0:
        return c
    return p if c <= 0 else min(c, p)


def _fmt_like(default: Any, value: float) -> Any:
    """أعِد القيمة بنوع الافتراض (عدد صحيح للعدّادات، نصّ «0.00» للمال)."""
    if isinstance(default, str):
        return f"{value:.2f}"
    return int(value)


def clamp_delegated_limits(parent_id: Optional[int], limits: dict, *,
                           tenant_id: int = 1) -> dict:
    """يقصّ حدود الابن المطلوبة على حدود الأب (``DELEGABLE_LIMIT_KEYS``) + تاريخ
    انتهاء المنح (لا يتجاوز تاريخ الأب). ``parent_id=None`` (المالك) = لا قصّ."""
    from .manager_distributor_ops import DEFAULT_LIMITS
    out: dict[str, Any] = {}
    plims = (_grants_row(parent_id, tenant_id).get("limits") or {}) if parent_id else {}
    for k, v in (limits or {}).items():
        if k in DELEGABLE_LIMIT_KEYS:
            val = clamp_to_parent_cap(v, plims.get(k)) if parent_id else _num(v)
            out[k] = _fmt_like(DEFAULT_LIMITS.get(k, 0), val)
        elif k == "grants_expire_at":
            want = str(v or "").strip()
            pexp = str(plims.get("grants_expire_at") or "").strip() if parent_id else ""
            if pexp and (not want or want[:19] > pexp[:19]):
                want = pexp
            out[k] = want
    return out


def clamp_delegated_credit(parent_id: Optional[int], requested: Any, *,
                           tenant_id: int = 1) -> str:
    """سقف ائتمان الابن ≤ سقف الأب (0 = بلا حدّ)."""
    if not parent_id:
        return f"{_num(requested):.2f}"
    try:
        from .manager_distributor_ops import ManagerDistributorOpsService
        pol = ManagerDistributorOpsService(tenant_id=int(tenant_id or 1)).get_policy(
            entity_type="manager", entity_id=int(parent_id))
        pcap = pol.get("credit_limit") or 0
    except Exception:  # noqa: BLE001 — تعذّر قراءة الأب = لا ائتمان للابن
        return "0.00" if _num(requested) <= 0 else f"{_num(requested):.2f}"
    return f"{clamp_to_parent_cap(requested, pcap):.2f}"


def clamp_delegation(parent_id: Optional[int], *, flags: Optional[dict] = None,
                     actions: Optional[dict] = None, tenant_id: int = 1) -> tuple[dict, dict]:
    """يَقصّ التفويض على ما يَملكه الأب فعليًّا (سقف التفويض الخادميّ):
    - أعلام can_*: يُمنح True للابن فقط إن كان الأب يَملكه؛ وإلّا False.
    - أفعال rbac: يُمنح override True فقط إن كان الفعل مسموحًا للأب؛ وإلّا يُطفأ.
    الإطفاء (False) مسموح دائمًا (الابن ≤ الأب). يُرجِع (flags_clamped, actions_clamped)."""
    out_flags: dict[str, bool] = {}
    for k, v in (flags or {}).items():
        want = bool(v)
        out_flags[k] = want and parent_has_grant(parent_id, "flag", k, tenant_id=tenant_id)
    out_actions: dict[str, bool] = {}
    for k, v in (actions or {}).items():
        want = bool(v)
        out_actions[k] = want and parent_has_grant(parent_id, "action", k, tenant_id=tenant_id)
    return out_flags, out_actions


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
        if spec.get("rbac_perm"):
            continue  # مُشتقّ من صلاحية RBAC — يُدار من مصفوفة «الصلاحيات» لا هنا
        if not spec.get("endpoints") and not spec.get("flag") and not spec.get("virtual"):
            continue  # فعل بلا مسار حقيقيّ ولا علَم ولا افتراضيّ (لا يُعرَض)
        if spec.get("entity_edit"):
            # D23: «تعديل/إضافة العرض» يُعرَضان في قسم «التحكّم بالحقول» بنفس
            # الاسم (action_edit_offer…) — تكرارهما هنا كان يُرسِل الاسم مرّتين.
            continue
        flag = spec.get("flag")
        if flag:
            input_name, kind = flag, "flag"
        else:
            input_name, kind = f"action_{key}", "rbac"
        by_section.setdefault(spec["section"], []).append({
            "key": key,
            "label": spec["label"],
            "input_name": input_name,
            "kind": kind,
            "checked": action_permitted(admin_id, key, tenant_id=tenant_id),
            "hint": spec.get("hint", ""),
        })
    out: list[dict[str, Any]] = []
    for sec, spec in MANAGER_SECTION_REGISTRY.items():
        if sec in by_section:
            out.append({"section": sec, "label": spec.get("label", sec),
                        "icon": spec.get("icon", "folder"), "actions": by_section[sec]})
    return out


# أعلام «نطاق الرؤية/الإشراف» التي ليست أفعالًا في ACTION_REGISTRY — تُعرَض
# في محرّر أساس الدور بقسم «نطاق الرؤية» وتُخزَّن في blob["flags"].
SCOPE_FLAG_REGISTRY: dict[str, str] = {
    "can_view_all_subscribers":  "عرض كل المشتركين",
    "can_view_all_card_batches": "عرض كل حزم البطاقات",
    "can_see_wholesale":         "رؤية سعر التكلفة/الجملة",
    "can_see_password":          "رؤية كلمة مرور المشترك",
    "can_see_balance":           "رؤية الرصيد والماليّات",
    "can_see_profit":            "رؤية الأرباح/الهامش",
    "can_create_sub_managers":   "إنشاء مدراء فرعيّين + تفويض",
}


def _blob_action_checked(blob: dict[str, Any], key: str, spec: dict[str, Any]) -> bool:
    """حالة فعلٍ من أساس دور (blob) — نفس منطق action_permitted لكن من dict."""
    flags = blob.get("flags") if isinstance(blob.get("flags"), dict) else {}
    ag = blob.get("action_grants") if isinstance(blob.get("action_grants"), dict) else {}
    flag = spec.get("flag")
    if flag:
        return bool(flags.get(flag))
    ent = spec.get("entity_edit")
    if ent:
        e = ag.get(ent) if isinstance(ag.get(ent), dict) else {}
        return bool(e.get(spec.get("entity_op", "edit")))
    acts = ag.get("_actions") if isinstance(ag.get("_actions"), dict) else {}
    ov = acts.get(key)
    return bool(ov) if ov is not None else bool(spec.get("default", True))


def role_action_catalog(blob: dict[str, Any]) -> list[dict[str, Any]]:
    """مصفوفة الأفعال مجمّعة بالقسم لمحرّر **أساس الدور** — حالتها من blob
    (لا من سياسة مدير). نفس بنية action_catalog وأسماء المُدخلات."""
    blob = blob or {}
    by_section: dict[str, list[dict[str, Any]]] = {}
    for key, spec in ACTION_REGISTRY.items():
        if spec.get("rbac_perm"):
            continue  # مُشتقّ من صلاحية RBAC — يُدار من مصفوفة «الصلاحيات» لا هنا
        if not spec.get("endpoints") and not spec.get("flag") and not spec.get("virtual"):
            continue
        flag = spec.get("flag")
        ent = spec.get("entity_edit")
        if flag:
            input_name, kind = flag, "flag"
        elif ent:
            # D23: اسمٌ لكل عمليّة (edit/create) — كان «إضافة عرض» يحمل اسم
            # action_edit_offer فيتكرّر مع «تعديل العرض».
            input_name, kind = f"action_{spec.get('entity_op', 'edit')}_{ent}", "entity_edit"
        else:
            input_name, kind = f"action_{key}", "rbac"
        by_section.setdefault(spec["section"], []).append({
            "key": key, "label": spec["label"], "input_name": input_name,
            "kind": kind, "checked": _blob_action_checked(blob, key, spec),
            "hint": spec.get("hint", ""),
        })
    out: list[dict[str, Any]] = []
    for sec, spec in MANAGER_SECTION_REGISTRY.items():
        if sec in by_section:
            out.append({"section": sec, "label": spec.get("label", sec),
                        "icon": spec.get("icon", "folder"), "actions": by_section[sec]})
    return out


def role_section_catalog(blob: dict[str, Any]) -> list[dict[str, Any]]:
    """قائمة الأقسام + حالتها (open/locked/hidden) من أساس دور (blob)."""
    blob = blob or {}
    states = blob.get("section_access") if isinstance(blob.get("section_access"), dict) else {}
    return [
        {"name": name, "label": spec.get("label", name),
         "icon": spec.get("icon", "folder"),
         "state": states.get(name, DEFAULT_SECTION_STATE)}
        for name, spec in MANAGER_SECTION_REGISTRY.items()
    ]


def parse_grants_form(form) -> dict[str, Any]:
    """يَبني أساس أفعال/رؤية/أقسام (blob) من حقول نموذج المحرّر. يُخزّن flags
    (أعلام can_*/الرؤية) + action_grants (_actions المخالفة للافتراض + بوّابات
    edit للكيانات) + section_access (الأقسام غير المفتوحة). لا يَشمل الحدود."""
    yes = {"1", "on", "true", "yes"}
    # D01: أعلام لها مربّع فقط (لا أعلام الأفعال المُشتقّة من RBAC)، ولا أعلام
    # نطاق الرؤية التي مصدرها مفتاح RBAC على الدور (D09).
    flags = {name: (form.get(name) in yes) for name in editable_flag_keys()
             if name not in ROLE_RBAC_SCOPE_FLAGS}
    actions: dict[str, bool] = {}
    for akey in rbac_action_keys():          # يستثني المُشتقّة (D01)
        checked = form.get(f"action_{akey}") in yes
        default = bool(ACTION_REGISTRY.get(akey, {}).get("default", True))
        if checked != default:                       # sparse: خزّن المخالف فقط
            actions[akey] = checked
    ag: dict[str, Any] = {"_actions": actions} if actions else {}
    for spec in ACTION_REGISTRY.values():
        entity = spec.get("entity_edit")
        if not entity or spec.get("rbac_perm"):
            continue
        op = spec.get("entity_op", "edit")
        if form.get(f"action_{op}_{entity}") in yes:
            ag.setdefault(entity, {})[op] = True
    # وصول الأقسام: خزّن غير-المفتوح فقط (open = الافتراض = وراثة/سلوك حاليّ).
    sections: dict[str, str] = {}
    for name in MANAGER_SECTION_REGISTRY:
        v = form.get(f"section_{name}")
        if v in SECTION_STATES and v != DEFAULT_SECTION_STATE:
            sections[name] = v
    blob: dict[str, Any] = {"flags": flags}
    if ag:
        blob["action_grants"] = ag
    if sections:
        blob["section_access"] = sections
    return blob


def set_action_override(admin_id: int, action_key: str, value: Optional[bool], *, tenant_id: int = 1) -> None:
    """يَضبط تجاوز فعلٍ يَحرسه RBAC (True/False)، أو يَحذفه (None=للافتراض)."""
    # يُكتب فوق صفّ المدير **الخام** — لا فوق النسخة المدموجة بأساس الدور
    # (كانت تنسخ منح الدور إلى صفّ المدير فتتجمّد وراثته، أو تمسح تجاوزاته
    # حين تنتهي منوحاته المؤقّتة).
    ag = dict(own_grants(admin_id, tenant_id=tenant_id).get("action_grants") or {})
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


# D03: صفحات GET تفتح **نموذج** إضافة/تعديل. القسم «المقفول (عرض فقط)» يرفضها
# قبل أن تُفتَح (وإلّا يملأ المدير النموذج ثم يُرفَض الحفظ وتضيع بياناته).
FORM_ENDPOINTS: frozenset = frozenset({
    "users_new", "users_edit", "plans_new", "bw_new", "devices_new",
    "card_users_add", "cards_recharge_new", "cards_print_new", "cards_batch_edit",
})


def is_form_endpoint(endpoint: str) -> bool:
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    return name in FORM_ENDPOINTS


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


# ─── وراثة الدور (2026-07): أساس الأفعال/الرؤية يُضبَط على الدور ويَرثه المدير ─
def _role_grants_for_admin(admin_id: Optional[int], tenant_id: int) -> dict[str, Any]:
    """أساس الأفعال/الرؤية الدقيق الموروث من **دور** المدير (لا من سياسته
    الفرديّة). يُرجع {} إن لا دور/لا أساس/خطأ (fail-open = لا وراثة، السلوك
    الحاليّ). خفيف: استعلام role_id مفرد ثمّ قراءة أساس الدور المخزَّن."""
    if not admin_id:
        return {}
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT role_id FROM admins WHERE id=?", (int(admin_id),)).fetchone()
        rid = int(row["role_id"]) if row and row["role_id"] else 0
        if not rid:
            return {}
        from ..db.repos import admins_repo
        role = admins_repo.get_role(rid)
        if admins_repo.role_is_super(role):
            # «مدير عام / سوبر يوزر» = كل الصلاحيات غير المقصورة على المالك:
            # كل الأعلام + كل الأفعال + كل الأقسام مفتوحة + بلا حصر حقول. التجاوز
            # الفرديّ للمدير (إن وُجد) يبقى فوقه كالعادة.
            return super_role_grants()
        if role is None:
            return {}
        blob = dict(admins_repo.get_role_granular(rid) or {})
        # D09/D14 — «مفتاحٌ واحد»: نطاق الرؤية على مستوى الدور = مفتاح RBAC
        # (scope.view_all_subscribers / scope.view_all_cards) في مصفوفة الدور،
        # لا علَمٌ موازٍ في أساس المنح. التجاوز الفرديّ للمدير يبقى فوقه.
        flags = dict(blob.get("flags") or {}) if isinstance(blob.get("flags"), dict) else {}
        rperms = set(getattr(role, "permissions", ()) or ())
        for flag, key in ROLE_RBAC_SCOPE_FLAGS.items():
            flags[flag] = key in rperms
        blob["flags"] = flags
        return blob
    except Exception:  # noqa: BLE001 — fail-open: لا وراثة على أيّ خطأ
        return {}


# D09: أعلام نطاق الرؤية التي مصدرها على **الدور** مفتاحُ RBAC (مربّعٌ واحد في
# مصفوفة الصلاحيات)، وعلى **المدير** تجاوزٌ فرديّ في صفحته. لا تُحفَظ في أساس الدور.
ROLE_RBAC_SCOPE_FLAGS: dict[str, str] = {
    "can_view_all_subscribers": "scope.view_all_subscribers",
    "can_view_all_card_batches": "scope.view_all_cards",
}


def super_role_grants() -> dict[str, Any]:
    """أساس الدور «مدير عام» المركَّب: كل شيء ممنوح (غير المقصور على المالك)."""
    from .manager_distributor_ops import DEFAULT_PERMISSIONS
    flags = {k: True for k in DEFAULT_PERMISSIONS}
    flags.update({k: True for k in SCOPE_FLAG_REGISTRY})
    actions: dict[str, Any] = {k: True for k in rbac_action_keys()}
    ag: dict[str, Any] = {"_actions": actions}
    for spec in ACTION_REGISTRY.values():
        ent = spec.get("entity_edit")
        if ent:
            ag.setdefault(ent, {})[spec.get("entity_op", "edit")] = True
    return {"flags": flags, "action_grants": ag}


def role_flags_for_admin(admin_id: Optional[int], *, tenant_id: int = 1) -> dict[str, Any]:
    """أعلام can_*/الرؤية الموروثة من دور المدير (لواجهات has_permission/العرض)."""
    g = _role_grants_for_admin(admin_id, tenant_id)
    f = g.get("flags")
    return {k: bool(v) for k, v in f.items()} if isinstance(f, dict) else {}


def role_sections_for_admin(admin_id: Optional[int], *, tenant_id: int = 1) -> dict[str, str]:
    """حالات الأقسام الموروثة من دور المدير (name→open/locked/hidden). للحفظ
    الفرديّ sparse-ضدّ-الدور ولواجهة العرض."""
    g = _role_grants_for_admin(admin_id, tenant_id)
    s = g.get("section_access")
    return {k: str(v) for k, v in s.items()} if isinstance(s, dict) else {}


def role_baseline_action(admin_id: Optional[int], action_key: str, *, tenant_id: int = 1) -> bool:
    """قيمة فعلٍ **الموروثة** للمدير (لو لا تجاوز فرديّ): أساس الدور إن ضبطه،
    وإلّا افتراض السجلّ. تُستخدَم في الحفظ لتخزين المخالف فقط (يَبقى الموروث)."""
    spec = ACTION_REGISTRY.get(action_key, {})
    rg = _role_grants_for_admin(admin_id, tenant_id)
    if rg:
        return _blob_action_checked(rg, action_key, spec)
    return bool(spec.get("default", True))


def _merge_grants(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """يَدمج أساس الدور (base) تحت تجاوزات المدير (over): المدير يَغلب لكلّ
    مفتاح ورقة. يَشمل flags/action_grants/section_access/field_grants فقط —
    **الحدود (limits) لا تُورَث** فتُؤخذ من المدير كما هي."""
    def _d(x): return x if isinstance(x, dict) else {}
    b_ag, o_ag = _d(base.get("action_grants")), _d(over.get("action_grants"))
    # action_grants: ادمج _actions (bool لكل فعل) + قواميس الكيانات (offer/batch)
    ag: dict[str, Any] = {}
    for src in (b_ag, o_ag):
        for k, v in src.items():
            if isinstance(v, dict):
                ag[k] = {**_d(ag.get(k)), **v}
            else:
                ag[k] = v
    # field_grants: {entity: {...}} — ادمج لكل كيان
    b_fg, o_fg = _d(base.get("field_grants")), _d(over.get("field_grants"))
    fg: dict[str, Any] = {}
    for src in (b_fg, o_fg):
        for k, v in src.items():
            fg[k] = {**_d(fg.get(k)), **v} if isinstance(v, dict) else v
    return {
        "flags": {**_d(base.get("flags")), **_d(over.get("flags"))},
        "action_grants": ag,
        "section_access": {**_d(base.get("section_access")), **_d(over.get("section_access"))},
        "field_grants": fg,
        "limits": _d(over.get("limits")),   # الحدود فرديّة دائمًا — لا وراثة
    }


def own_grants(admin_id: Optional[int], *, tenant_id: int = 1) -> dict[str, Any]:
    """تجاوزات المدير الفرديّة **كما خُزِّنت** (بلا دمج أساس الدور وبلا إلغاء
    الانتهاء) — مصدر الكتابة ومصدر حالة «حسب الدور/مسموح/ممنوع» في الصفحة."""
    out = {"section_access": {}, "action_grants": {}, "field_grants": {},
           "flags": {}, "limits": {}}
    if not admin_id:
        return out
    try:
        from ..db.connection import db
        row = db().execute(
            """
            SELECT section_access_json, action_grants_json, field_grants_json,
                   permissions_json, limits_json
            FROM manager_distributor_policies
            WHERE tenant_id=? AND entity_type='manager' AND entity_id=?
            """,
            (int(tenant_id or 1), int(admin_id)),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return out
    if row:
        out = {
            "section_access": _load(row["section_access_json"]),
            "action_grants": _load(row["action_grants_json"]),
            "field_grants": _load(row["field_grants_json"]),
            "flags": _load(row["permissions_json"]),
            "limits": _load(row["limits_json"]),
        }
    return out


def _grants_row(admin_id: Optional[int], tenant_id: int) -> dict[str, Any]:
    """يُرجع {section_access, action_grants, field_grants, flags, limits} —
    **الأساس الموروث من الدور مدموجٌ تحت تجاوزات المدير الفرديّة**. مخزَّن لكل طلب.

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
    # المرحلة F: المنوحات المؤقّتة — بعد تاريخ الانتهاء تُلغى مَنوحات المدير
    # (الأعلام can_* + بوّابات الأفعال) فيَعود للأساس المقيَّد. القيود (الحدود/
    # إخفاء الأقسام/التحكّم الحقليّ) تَبقى. السوبر يُعالَج قبل الوصول هنا.
    if _grants_are_expired((val.get("limits") or {}).get("grants_expire_at")):
        val = dict(val)
        val["flags"] = {}
        val["action_grants"] = {}
    # وراثة الدور: ادمج أساس الدور تحت تجاوزات المدير (بعد تطبيق الانتهاء، فيَعود
    # المدير المنتهي إلى أساس دوره لا إلى فراغ). الحدود تبقى فرديّة. غياب أساس
    # الدور = لا تغيير (السلوك الحاليّ).
    rg = _role_grants_for_admin(admin_id, tenant_id)
    if rg:
        val = _merge_grants(rg, val)
    try:
        g._mg_grants_cache = {"_key": key, "val": val}
    except Exception:  # noqa: BLE001 — خارج سياق الطلب (اختبارات/CLI)
        pass
    return val


def _grants_are_expired(raw: Any) -> bool:
    """هل تجاوز «تاريخ انتهاء المنوحات» الآن؟ يَقبل YYYY-MM-DD أو ISO. فارغ/
    غير صالح = لا انتهاء (fail-open: لا نُلغي منوحات على قيمة معطوبة)."""
    s = str(raw or "").strip()
    if not s:
        return False
    from datetime import datetime, date
    try:
        if len(s) == 10:  # YYYY-MM-DD → ينتهي بنهاية ذلك اليوم
            d = date.fromisoformat(s)
            from ..core.system_config import local_today
            return local_today() > d  # نهاية اليوم **المحلّيّ** للّوحة
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.utcnow()
        return now > dt
    except (ValueError, TypeError):
        return False


def grants_expired(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """هل انتهت مَنوحات المدير المؤقّتة؟ (لواجهة العرض)."""
    # نقرأ limits مباشرةً (قبل تطبيق الإلغاء) عبر خدمة السياسة كي لا يُخفيها
    # _grants_row المُطبَّق عليه الإلغاء أصلًا — الحدود تَبقى فلا فرق هنا.
    lims = _grants_row(admin_id, tenant_id).get("limits") or {}
    return _grants_are_expired(lims.get("grants_expire_at"))


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
    ManagerDistributorOpsService(tenant_id=int(tenant_id or 1)).ensure_row(
        entity_type="manager", entity_id=int(admin_id))


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


def reset_overrides_to_role(admin_id: int, *, tenant_id: int = 1) -> None:
    """يُزيل كلّ تجاوزات المدير الفرديّة (الأعلام + بوّابات الأفعال + وصول
    الأقسام + التحكّم الحقليّ) فيَعود لوراثة أساس دوره بالكامل. الحدود الرقميّة
    والائتمان تبقى فرديّة (لا تُمَسّ)."""
    _ensure_policy_row(int(admin_id), tenant_id)
    from ..db.connection import db
    from ..db.helpers import now_iso
    db().execute(
        """
        UPDATE manager_distributor_policies
        SET permissions_json='{}', action_grants_json='{}',
            section_access_json='{}', field_grants_json='{}', updated_at=?
        WHERE tenant_id=? AND entity_type='manager' AND entity_id=?
        """,
        (now_iso(), int(tenant_id or 1), int(admin_id)),
    )
    _invalidate_cache()


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


# D19: الحقول التي لا معنى لإنشاء مشتركٍ بدونها — لا تُعاد عند الإنشاء (يُحكَم
# تعديلها لاحقًا فقط). الرصيد لا يُضبط عند الإنشاء أبدًا لغير المالك.
CREATE_ALWAYS_ALLOWED: dict[str, tuple[str, ...]] = {
    "subscriber": ("username", "password"),
}


def enforce_create(admin_id: Optional[int], entity: str, incoming, default, *, tenant_id: int = 1):
    """D19 — التحكّم الحقليّ عند **الإنشاء**: الحقول غير الممنوحة تأخذ قيمة
    ``default`` (نموذج الإنشاء الفارغ: الحالة الافتراضيّة، المدير المسؤول = المُنشئ،
    بلا سعر مخصّص…) بدل ما أرسله المدير. اسم الدخول/كلمة المرور مستثنيان."""
    reverts = reverted_attrs(admin_id, entity, tenant_id=tenant_id) - set(
        CREATE_ALWAYS_ALLOWED.get(entity, ()))
    if not reverts or default is None:
        return incoming
    from dataclasses import replace
    patch = {a: getattr(default, a) for a in reverts
             if hasattr(default, a) and hasattr(incoming, a)}
    return replace(incoming, **patch) if patch else incoming


def locked_changes(admin_id: Optional[int], entity: str, submitted, kept, *,
                   tenant_id: int = 1) -> list[str]:
    """D20: تسميات الحقول المقفولة التي أرسل المدير لها قيمةً مختلفة فأُعيدت —
    لرسالة «لم تُحفَظ الحقول المقفولة» بدل «تم التحديث» الصامت."""
    granted = field_grants(admin_id, entity, tenant_id=tenant_id)
    if granted is None:
        return []
    out = []
    for fdef in FIELD_REGISTRY.get(entity, ()):
        if fdef["key"] in granted:
            continue
        if any(getattr(submitted, a, None) != getattr(kept, a, None)
               for a in fdef["attrs"] if hasattr(submitted, a)):
            out.append(fdef["label"])
    return out


def locked_attr_names(admin_id: Optional[int], entity: str, *, tenant_id: int = 1) -> list[str]:
    """D20: أسماء حقول النموذج المقفولة (للعرض للقراءة مع تلميح القفل)."""
    return sorted(reverted_attrs(admin_id, entity, tenant_id=tenant_id))


def set_field_grants(
    admin_id: int, entity: str, fields: Optional[Iterable[str]], *, tenant_id: int = 1
) -> None:
    """يَضبط الحقول المسموحة لكيان. ``fields=None`` يُطفئ التحكّم (يَحذف المفتاح)."""
    current = dict(own_grants(admin_id, tenant_id=tenant_id).get("field_grants") or {})
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
    current = dict(own_grants(admin_id, tenant_id=tenant_id).get("action_grants") or {})
    if actions is None:
        current.pop(entity, None)
    else:
        current[entity] = {k: bool(v) for k, v in actions.items()}
    _ensure_policy_row(int(admin_id), tenant_id)
    _write_column(int(admin_id), tenant_id, "action_grants_json", current)
    _invalidate_cache()



# ═══ «حسب الدور / مسموح / ممنوع» — تجاوزٌ فرديّ ثلاثيّ لكل فعلٍ وعلَم ═════════
# (fix wave 2 — متابعة المالك) بعد توحيد الأفعال المُشتقّة مع مفاتيح الدور صار
# مفتاح store.review يمنح «تأكيد الإيداع» و«تأكيد السحب» معًا، و online.disconnect
# يمنح «قطع الجلسة» و«الإغلاق الإجباريّ» معًا — دون طريقٍ في الصفحة لقول «إيداع
# نعم / سحب لا» لمديرٍ واحد. هنا يُعرَض كل فعلٍ وعلَم بثلاث حالات:
#   • «حسب الدور»  (الافتراض) = لا تجاوز مخزَّن — يرث ما يمنحه دوره الآن.
#   • «مسموح»      = تجاوزٌ صريح True.
#   • «ممنوع»      = تجاوزٌ صريح False — يَمنع هذا الفعل وحده ولو منحه الدور.
# التخزين sparse: «حسب الدور» يحذف التجاوز؛ فحفظ الصفحة بلا تغيير لا يغيّر شيئًا
# (D01/D02). الفعل المُشتقّ من مفتاح RBAC سقفُه المفتاح: «مسموح» يُعيد فعلًا
# أطفأه «أساس الدور» لكنه لا يتخطّى مفتاحًا لا يملكه الدور (مسارات الفعل نفسها
# محروسةٌ بالمفتاح في الويب والـAPI) — فيُعطَّل خياره في الصفحة مع السبب.
TRI_INHERIT, TRI_ALLOW, TRI_DENY = "inherit", "allow", "deny"
TRI_STATES = (TRI_INHERIT, TRI_ALLOW, TRI_DENY)
TRI_PREFIX = "tri_"
_TRI_WORD = {True: "مسموح", False: "ممنوع"}
VISIBILITY_GROUP = "_visibility"


def tri_input_name(key: str) -> str:
    return TRI_PREFIX + key


def tri_rows() -> list[dict[str, Any]]:
    """كل صفّ قابل للتجاوز الفرديّ: الأفعال (كل ACTION_REGISTRY المعروض) ثم أعلام
    نطاق الرؤية. ``store`` = أين يُخزَّن التجاوز:
      ("action", key)       → action_grants._actions[key]
      ("flag", flag)        → permissions_json[flag]
      ("entity", ent, op)   → action_grants[ent][op]"""
    rows: list[dict[str, Any]] = []
    for key, spec in ACTION_REGISTRY.items():
        if not spec.get("endpoints") and not spec.get("flag") and not spec.get("virtual"):
            continue
        rbac = spec.get("rbac_perm")
        if rbac:
            store: tuple = ("action", key)
        elif spec.get("flag"):
            store = ("flag", spec["flag"])
        elif spec.get("entity_edit"):
            store = ("entity", spec["entity_edit"], spec.get("entity_op", "edit"))
        else:
            store = ("action", key)
        rows.append({"key": key, "label": spec["label"], "section": spec["section"],
                     "store": store, "rbac": rbac})
    for flag, label in SCOPE_FLAG_REGISTRY.items():
        rows.append({"key": flag, "label": label, "section": VISIBILITY_GROUP,
                     "store": ("flag", flag), "rbac": None})
    return rows


def _tri_get(blob: dict[str, Any], store: tuple) -> Optional[bool]:
    """قيمة التجاوز المخزَّنة في blob (صفّ مدير خام أو أساس دور) أو None."""
    ag = blob.get("action_grants") if isinstance(blob.get("action_grants"), dict) else {}
    if store[0] == "flag":
        flags = blob.get("flags") if isinstance(blob.get("flags"), dict) else {}
        v = flags.get(store[1])
    elif store[0] == "action":
        acts = ag.get("_actions") if isinstance(ag.get("_actions"), dict) else {}
        v = acts.get(store[1])
    else:
        ent = ag.get(store[1]) if isinstance(ag.get(store[1]), dict) else {}
        v = ent.get(store[2])
    return None if v is None else bool(v)


def tri_state_of(value: Optional[bool]) -> str:
    return TRI_INHERIT if value is None else (TRI_ALLOW if value else TRI_DENY)


def tri_value_of(state: Any) -> tuple[bool, Optional[bool]]:
    """(صالح؟, القيمة) لحالةٍ مُرسَلة: inherit→None، allow→True، deny→False."""
    s = str(state or "").strip().lower()
    if s not in TRI_STATES:
        return False, None
    return True, (None if s == TRI_INHERIT else s == TRI_ALLOW)


def _tri_role_value(admin_id: Optional[int], row: dict[str, Any], rg: dict[str, Any],
                    tenant_id: int) -> tuple[bool, str]:
    """ما يمنحه **الدور** لهذا الصفّ لو لا تجاوز (القيمة، سببٌ عربيّ مختصر)."""
    store = row["store"]
    if row.get("rbac"):
        if not _rbac_any(row["rbac"], _admin_rbac_perms(admin_id, tenant_id)):
            return False, "ينقص الدور المفتاح " + rbac_perm_label(row["key"])
        if _tri_get(rg, ("action", row["key"])) is False:
            return False, "مُطفأ في أساس الدور"
        return True, ""
    v = _tri_get(rg, store)
    if v is not None:
        return v, ""
    if store[0] == "flag":
        from .manager_distributor_ops import DEFAULT_PERMISSIONS
        return bool(DEFAULT_PERMISSIONS.get(store[1], False)), ""
    if store[0] == "entity":
        return False, ""
    return bool(ACTION_REGISTRY.get(row["key"], {}).get("default", True)), ""


def _tri_effective(admin_id: Optional[int], row: dict[str, Any], tenant_id: int) -> bool:
    if row["section"] == VISIBILITY_GROUP:
        return bool((_grants_row(admin_id, tenant_id).get("flags") or {}).get(row["store"][1]))
    return bool(action_permitted(admin_id, row["key"], tenant_id=tenant_id))


def tristate_catalog(admin_id: Optional[int], *, tenant_id: int = 1) -> list[dict[str, Any]]:
    """مصفوفة «حسب الدور/مسموح/ممنوع» لصفحة المدير — مجموعة «نطاق الرؤية» ثم
    الأقسام. كل صفّ: الحالة المخزَّنة، قيمة الدور الحاليّة (تُعرَض بجانبه)،
    القرار الفعليّ، وهل «مسموح» معطَّل (فعلٌ مُشتقّ ومفتاحه غير ممنوح للدور)."""
    _invalidate_cache()
    own = own_grants(admin_id, tenant_id=tenant_id)
    rg = _role_grants_for_admin(admin_id, tenant_id)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in tri_rows():
        stored = _tri_get(own, row["store"])
        role_val, role_why = _tri_role_value(admin_id, row, rg, tenant_id)
        key_missing = bool(row.get("rbac")) and not _rbac_any(
            row["rbac"], _admin_rbac_perms(admin_id, tenant_id))
        groups.setdefault(row["section"], []).append({
            "key": row["key"],
            "input": tri_input_name(row["key"]),
            "label": row["label"],
            "state": tri_state_of(stored),
            "role_value": role_val,
            "role_label": _TRI_WORD[role_val] + (f" — {role_why}" if role_why else ""),
            "effective": _tri_effective(admin_id, row, tenant_id),
            "derived": bool(row.get("rbac")),
            "rbac_label": rbac_perm_label(row["key"]) if row.get("rbac") else "",
            # «مسموح» لا يتخطّى مفتاح الدور: يُعطَّل ما لم يكن مخزَّنًا أصلًا (فيبقى
            # ظاهرًا مع التحذير ويمكن تغييره).
            "allow_disabled": key_missing and stored is not True,
            "allow_ineffective": key_missing and stored is True,
        })
    out: list[dict[str, Any]] = []
    if VISIBILITY_GROUP in groups:
        out.append({"section": VISIBILITY_GROUP, "label": "نطاق الرؤية والحماية الماليّة",
                    "icon": "eye", "rows": groups[VISIBILITY_GROUP]})
    for sec, spec in MANAGER_SECTION_REGISTRY.items():
        if sec in groups:
            out.append({"section": sec, "label": spec.get("label", sec),
                        "icon": spec.get("icon", "folder"), "rows": groups[sec]})
    return out


def _tri_actor_has(actor_id: int, row: dict[str, Any], tenant_id: int) -> bool:
    try:
        return _tri_effective(int(actor_id), row, tenant_id)
    except Exception:  # noqa: BLE001 — لا منح على خطأ
        return False


def save_manager_overrides(admin_id: int, form, *, tenant_id: int = 1,
                           actor_id: Optional[int] = None,
                           actor_owner: bool = True) -> dict[str, Any]:
    """يحفظ تجاوزات المدير الفرديّة من نموذج صفحته (الأعلام + الأفعال + بوّابات
    الكيانات). لكل صفّ: ``tri_<key>`` إن أُرسِل (inherit/allow/deny)، وإلّا
    **عقد المربّعات القديم** كما كان (عملاء/اختبارات قديمة): العلَم/الفعل
    المُرسَل مؤشَّرًا يُقارن بأساس الدور ويُخزَّن المخالف فقط؛ الأفعال المُشتقّة
    بلا حقل ثلاثيّ لا تُمسّ، وبوّابات الكيانات القديمة (action_edit_<entity>)
    يعالجها المستدعي (``legacy_entities``).

    يكتب action_grants_json مباشرةً ويُرجع ``{"flags", "refused", "legacy_entities"}``
    حيث flags = أعلام المدير الـsparse الجديدة (يكتبها المستدعي عبر set_policy)
    و refused = تسميات «مسموح» التي رُفضت لأنّ الفاعل (غير المالك) لا يملكها."""
    yes = {"1", "on", "true", "yes"}
    aid = int(admin_id)
    own = own_grants(aid, tenant_id=tenant_id)
    rg = _role_grants_for_admin(aid, tenant_id)
    old_flags = dict(own.get("flags") or {})
    ag = {k: (dict(v) if isinstance(v, dict) else v)
          for k, v in (own.get("action_grants") or {}).items()}
    acts = dict(ag.get("_actions") or {}) if isinstance(ag.get("_actions"), dict) else {}
    editable = set(editable_flag_keys())
    # أعلام can_* القابلة للتحرير فقط (D01: أعلام الأفعال المُشتقّة لا تُخزَّن)،
    # والمدير يبدأ من تجاوزاته الصريحة القائمة.
    flags: dict[str, bool] = {k: bool(v) for k, v in old_flags.items() if k in editable}
    refused: list[str] = []
    legacy_entities: set[str] = set()
    for row in tri_rows():
        store = row["store"]
        field = tri_input_name(row["key"])
        if field in form:
            ok, val = tri_value_of(form.get(field))
            if not ok:
                continue
        else:
            # ── عقد المربّعات القديم ──
            if row.get("rbac"):
                continue                    # مُشتقّ بلا حقل ثلاثيّ: لا يُمسّ (D01)
            if store[0] == "flag":
                if store[1] not in editable:
                    continue
                desired = form.get(store[1]) in yes
                role_val = _tri_get(rg, store)
                if role_val is None:
                    from .manager_distributor_ops import DEFAULT_PERMISSIONS
                    role_val = bool(DEFAULT_PERMISSIONS.get(store[1], False))
                val = None if desired == role_val else desired
            elif store[0] == "entity":
                legacy_entities.add(store[1])
                continue                    # يعالجه المستدعي (action_edit_<entity>)
            else:
                checked = form.get(f"action_{row['key']}") in yes
                val = None if checked == role_baseline_action(
                    aid, row["key"], tenant_id=tenant_id) else checked
        # سقف الفاعل: غير المالك لا يمنح «مسموح» صريحًا لما لا يملكه هو.
        if val is True and not actor_owner and actor_id \
                and _tri_get(own, store) is not True \
                and not _tri_actor_has(int(actor_id), row, tenant_id):
            refused.append(row["label"])
            continue
        if store[0] == "flag":
            if val is None:
                flags.pop(store[1], None)
            else:
                flags[store[1]] = bool(val)
        elif store[0] == "action":
            if val is None:
                acts.pop(store[1], None)
            else:
                acts[store[1]] = bool(val)
            if row["key"] == "batch.edit":
                # الحقل الثلاثيّ يملك «تعديل الحزمة» الآن — تُزال منحة الكيان
                # القديمة (التي كانت تسمح دون المفتاح) كي لا يبقى مصدران.
                ent = ag.get("batch") if isinstance(ag.get("batch"), dict) else None
                if ent is not None:
                    ent.pop("edit", None)
                    if ent:
                        ag["batch"] = ent
                    else:
                        ag.pop("batch", None)
        else:
            ent = dict(ag.get(store[1]) or {}) if isinstance(ag.get(store[1]), dict) else {}
            if val is None:
                ent.pop(store[2], None)
            else:
                ent[store[2]] = bool(val)
            if ent:
                ag[store[1]] = ent
            else:
                ag.pop(store[1], None)
    if acts:
        ag["_actions"] = acts
    else:
        ag.pop("_actions", None)
    _ensure_policy_row(aid, tenant_id)
    _write_column(aid, tenant_id, "action_grants_json", ag)
    _invalidate_cache()
    return {"flags": flags, "refused": refused, "legacy_entities": legacy_entities}


def role_derived_catalog(blob: dict[str, Any], role_perms) -> list[dict[str, Any]]:
    """تقسيم الأفعال المُشتقّة على **أساس الدور**: لكل فعلٍ مفتاحُه في مصفوفة
    الصلاحيات أعلاه، وهنا «حسب المفتاح» (الافتراض) / «مسموح» / «ممنوع» — مثل
    «تأكيد الإيداع نعم / تأكيد السحب لا» لكل مدراء الدور. «مسموح» لا يتخطّى
    المفتاح (يُعطَّل حين لا يملكه الدور)."""
    blob = blob or {}
    perms = set(role_perms or ())
    by_section: dict[str, list[dict[str, Any]]] = {}
    for row in tri_rows():
        if not row.get("rbac"):
            continue
        stored = _tri_get(blob, ("action", row["key"]))
        has_key = _rbac_any(row["rbac"], perms)
        by_section.setdefault(row["section"], []).append({
            "key": row["key"], "input": tri_input_name(row["key"]),
            "label": row["label"], "state": tri_state_of(stored),
            "rbac_label": rbac_perm_label(row["key"]), "has_key": has_key,
            "key_label": "ممنوح" if has_key else "غير ممنوح",
            "allow_disabled": (not has_key) and stored is not True,
            "effective": bool(has_key and stored is not False),
        })
    out: list[dict[str, Any]] = []
    for sec, spec in MANAGER_SECTION_REGISTRY.items():
        if sec in by_section:
            out.append({"section": sec, "label": spec.get("label", sec),
                        "icon": spec.get("icon", "folder"), "rows": by_section[sec]})
    return out


def apply_role_derived_form(blob: dict[str, Any], form,
                            existing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """يضيف تقسيم الأفعال المُشتقّة إلى أساس دور مبنيّ من النموذج: ``tri_<key>``
    المُرسَل يحكم (inherit يحذف)؛ غير المُرسَل يحتفظ بقيمة الأساس القائم (نموذج
    قديم لا يمسح تقسيمًا ضبطه المالك)."""
    existing = existing or {}
    ag = dict(blob.get("action_grants") or {})
    acts = dict(ag.get("_actions") or {})
    for row in tri_rows():
        if not row.get("rbac"):
            continue
        field = tri_input_name(row["key"])
        if field in form:
            ok, val = tri_value_of(form.get(field))
            if not ok:
                continue
        else:
            val = _tri_get(existing, ("action", row["key"]))
        if val is None:
            acts.pop(row["key"], None)
        else:
            acts[row["key"]] = bool(val)
    if acts:
        ag["_actions"] = acts
    else:
        ag.pop("_actions", None)
    out = dict(blob)
    if ag:
        out["action_grants"] = ag
    else:
        out.pop("action_grants", None)
    return out


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
    "DELEGABLE_LIMIT_KEYS", "clamp_to_parent_cap", "clamp_delegated_limits",
    "clamp_delegated_credit",
    "action_catalog", "section_has_capability", "effective_section_hidden",
    "endpoint_effectively_hidden",
    "LIMIT_KEYS", "limit_value", "manager_subscriber_count", "manager_card_count",
    "subscriber_cap_blocked", "card_cap_block_reason", "limits_catalog",
    "VISIBILITY_REGISTRY", "can_see", "visibility_keys",
    "BULK_ENDPOINTS", "bulk_blocked", "grants_expired",
    "FORM_ENDPOINTS", "is_form_endpoint",
    "enforce_create", "locked_changes", "locked_attr_names", "CREATE_ALWAYS_ALLOWED",
    "editable_flag_keys", "derived_action_keys", "is_derived_action",
    "rbac_perm_label", "FLAG_DERIVED_ACTION", "super_role_grants",
    "ROLE_RBAC_SCOPE_FLAGS",
    "parent_admin_id", "can_create_sub_managers", "parent_has_grant", "clamp_delegation",
    "own_grants", "TRI_INHERIT", "TRI_ALLOW", "TRI_DENY", "TRI_STATES", "tri_rows",
    "tri_input_name", "tri_state_of", "tri_value_of", "tristate_catalog",
    "save_manager_overrides", "role_derived_catalog", "apply_role_derived_form",
]
