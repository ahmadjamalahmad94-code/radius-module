"""
صلاحيات الواجهة (UI Permissions) — مساعدات Jinja لإخفاء/تجميد عناصر
الواجهة حسب صلاحيات المسؤول الحالي.

الفكرة: الحارس الخادمي (_PERM_GUARDED في routes/blueprint.py) يمنع 403
على المسارات الحسّاسة، لكن الواجهة كانت تعرض كل شيء للجميع. هنا نوفّر
ثلاث دوال تُحقن في كل القوالب عبر context processor التطبيق:

  • can(perm_key)        → هل يملك المسؤول الحالي هذه الصلاحية؟
                            (super_admin يمرّ دائمًا — نفس عقد الحارس)
  • ui_unauth_mode()     → "freeze" أو "hide": كيف تُعرض الأقسام غير
                            المصرّح بها؟ يقرّرها مالك الريدياس من
                            إعدادات النظام (security.unauthorized_ui).
  • perm_for_endpoint(e) → مفتاح الصلاحية المطلوب لبند تنقّل يشير إلى
                            endpoint معيّن (للـ sidebar) — يُحلّ أولًا من
                            خريطة التنقّل _NAV_PERM ثم من _PERM_GUARDED.

كل القراءات مخزّنة لكل طلب (flask.g) — لا استعلام DB إضافي بعد أول
استدعاء، والصلاحيات نفسها تأتي من session["permissions"] المحمَّلة عند
تسجيل الدخول (auth/session_helpers.set_current_admin).
"""
from __future__ import annotations

from flask import g, session

# نفس قيمة الحارس في routes/blueprint.py — صلاحية super_admin فقط
_PERM_SUPER = "__super__"

# مفتاح الإعداد + قيمه المعتمدة — يضبطه المالك من صفحة إعدادات النظام
UNAUTH_UI_SETTING_KEY = "security.unauthorized_ui"
UNAUTH_UI_DEFAULT = "freeze"          # الافتراضي: تجميد بقفل (يرى ولا يدخل)
_UNAUTH_UI_VALID = {"freeze", "hide"}

# ─── خريطة التنقّل: endpoint (بدون بادئة radius.) → مفتاح الصلاحية ───
# _PERM_GUARDED يحرس الكتابة غالبًا؛ صفحات «العرض» نفسها غير محروسة على
# الخادم، لكن للواجهة نربط كل بند تنقّل بأقرب مفتاح عرض منطقي حتى يرى
# كل دور أقسامه فقط. endpoint غير موجود هنا ولا في _PERM_GUARDED
# = بند أساسي مفتوح للجميع (لوحة التحكم، مركز الأدلة...).
_NAV_PERM: dict[str, str] = {
    # ── المشتركون ──
    "subscribers_overview": "users.view",
    "subscribers_list": "users.view",
    "users_new": "users.create",
    "subscriber_groups_list": "users.view",
    "online_list": "online.view",

    # ── البطاقات ──
    "cards_overview": "cards.view",
    "cards_checker": "cards.verify",      # GET للعرض غير محروس خادميًا — للواجهة نربطه بمفتاحه
    "cards_batches": "cards.view",
    "cards_generate": "cards.generate",
    "cards_print_list": "cards.print",
    "print_templates": "cards.print",

    # ── البطاقات الإلكترونية (المتجر) — مفتاح عرض مستقلّ store.view ──
    # كان مربوطًا بـcards.view فيَظهر المتجر لأيّ من يرى البطاقات (تسريب).
    # الآن store.view يَفصله: مدير عنده «عرض البطاقات» فقط لن يرى المتجر.
    # الحارس المركزيّ (blueprint step 2) يَفرض هذا على GET بالعنوان المباشر.
    "card_marketplace": "store.view",
    "card_users_list": "store.view",
    "card_user_360": "store.view",
    "card_marketplace_package_file": "store.view",
    "cards_recharge_list": "cards.recharge",

    # ── التحكم بالسرعة ──
    "operations_speed_control": "users.temp_speed",
    "operations_speed_control_manual": "users.temp_speed",

    # ── العروض والسرعات ──
    "plans_overview": "plans.view",
    "plans_list": "plans.view",
    "plans_new": "plans.create",
    "bw_list": "plans.view",
    "bandwidth_schedules": "plans.view",

    # ── الشبكة ──
    "mt_setup_form": "nas.create",
    "setup_wizard_v3_page": "nas.create",
    "devices_list": "nas.view",
    "mt_operations": "nas.view",
    "mt_operations_live": "nas.view",
    "services_catalog": "nas.view",
    "pool_list": "nas.view",
    # رسائل أخطاء الهوتسبوت — تُرفع للراوترات، عرضها بمفتاح عرض الراوترات
    "hotspot_errors_page": "nas.view",
    "mt_alerts_index": "nas.view",
    "audit_log_index": "audit.view",
    "mt_push_setup": "nas.edit",
    # نفق إدارة v6 (SSTP/PPTP): العرض/التشخيص بمفتاح عرض الراوترات،
    # والتعديل (مزامنة/كلمة مرور/تفعيل/صلاحية/حذف) بمفتاح تعديل الراوترات.
    "mt_sstp_users": "nas.view",
    "mt_sstp_credentials": "nas.view",
    # توليد سكربت التهيئة الكامل (يكشف أسرارًا + يُهيّئ حساب النفق) — مفتاح إنشاء.
    "mt_onboarding_script": "nas.create",
    # «فتح WinBox» يكشف إدارة الراوتر عبر منفذ عام — super-admin حصرًا.
    "mt_remote_winbox_open": _PERM_SUPER,
    "mt_remote_close": _PERM_SUPER,
    "mt_remote_sessions": _PERM_SUPER,
    "mt_sstp_test": "nas.view",
    "mt_sstp_sync": "nas.edit",
    "mt_sstp_reset": "nas.edit",
    "mt_sstp_user_toggle": "nas.edit",
    "mt_sstp_user_expiry": "nas.edit",
    "mt_sstp_user_reset": "nas.edit",
    "mt_sstp_user_delete": "nas.edit",
    # نفق إدارة v7 (WireGuard): العرض بمفتاح عرض الراوترات، والتعديل
    # (توليد المفاتيح/إزالة peer) بمفتاح تعديل الراوترات — موازٍ لـSSTP.
    "mt_wg_peers": "nas.view",
    "mt_wg_peer_regenerate": "nas.edit",
    "mt_wg_peer_remove": "nas.edit",

    # ── المال والتحصيل ──
    # لوحة الشحن قراءاتها خفيفة وكل عملياتها تمرّ عبر مسارات users/accounting
    # المحروسة — مفتاح عرضها الطبيعي هو دفعات المشتركين.
    "recharge_panel": "users.payments",
    "finance_center_hub": "reports.finance",
    "accounting_hub": "reports.finance",
    "billing_hub": "reports.finance",
    # التحصيل والمدفوعات + مختبر الدفع — صفحات «المزوّد فقط» (تنظيف لوحة
    # العميل، يونيو 2026): التحصيل مجمّد ومختبر الدفع تجريبي (WIP)، يتحكّم
    # بهما المزوّد من لوحة التراخيص. super_admin فقط — فيُخفيان من الشريط
    # الجانبي لغير السوبر (nav_can=false) ويُطابقان حارس المسار في blueprint.
    "collection_hub": _PERM_SUPER,
    "payments_lab": _PERM_SUPER,
    "company_inventory": "reports.finance",

    # ── التشغيل والمخاطر ──
    "communications": "users.send_message",
    "whatsapp": "users.send_message",
    "events_center": "audit.view",
    "operations_center": "audit.view",

    # ── التقارير ── (الحارس الخادمي يغطيها أصلًا — نكررها للوضوح فقط
    #    عند الحاجة؛ perm_for_endpoint يرجع لـ _PERM_GUARDED تلقائيًا)
    # reports_* / rep_* → reports.view عبر _PERM_GUARDED
    # finance_reports   → reports.finance عبر _PERM_GUARDED

    # ── الدعم ──
    "tk_list": "users.view",
    "svc_list": "users.view",
    "customer_portals_admin": "users.view",

    # ── الإدارة ──
    "business_operators": "admins.view",
    # لوحة شحن الرصيد — المالك الرئيسي فقط (يطابق حارس المسار _PERM_SUPER).
    "credit_dashboard": _PERM_SUPER,
    "admin_pricing_page": "admin_pricing.view",
    "roles_list": _PERM_SUPER,
    # لوحة التراخيص — خدمة نفق تغيير IP المدفوعة (RBAC: licensing.view)
    "licensing_index": "licensing.view",
    "backups": _PERM_SUPER,
    "settings_page": "settings.view",
    "access_control_page": "settings.view",
    "anti_mac_clone_page": "settings.view",
    "sync_list": "nas.view",
    "tenants_list": _PERM_SUPER,
    # حالة منح المزوّد — تشخيصية، يفضّل قصرها على السوبر لأنّها تكشف
    # محتوى عقد التراخيص. صفحة blocked عامة (تظهر لمن يصل لخدمة موقوفة).
    "provider_grants_status_page": _PERM_SUPER,
    # «ربط وتفعيل النسخة» — حساس (تخزين المفتاح + reset يَمسح اللقطات).
    # super_admin فقط (مثل license_file السابق). ملاحظة: الصفحة نفسها
    # مستثناة من lifecycle gate كي تَبقى متاحة على نسخة مقفلة.
    "license_connect_page": _PERM_SUPER,

    # ── التكامل والجسر ──
    "admin_bridge": "api.use",
    "license_file": "api.use",
    "tunnels_list": "api.use",
    "wh_settings": "api.use",
    "tok_list": "api.use",

    # ═══ تدقيق QA RBAC (2026-06): صفحات «عرض» (GET) كانت بلا حارس عرض ═══
    # تُربط بمفتاح القسم المنطقيّ كي يُطبَّق «حارس العرض» (blueprint._perm_guard
    # خطوة 2) فلا يَصِلها viewer مباشرةً بالعنوان. الكتابات المقابلة محروسة
    # في _PERM_GUARDED. super_admin/المدير الرئيسي يتجاوز دائمًا.
    "admins_list": "admins.view",
    "admins_profile_summary": "admins.view",
    "cards_list": "cards.view",
    # ملاحظة: cards_checker_v2 (عرض الفاحص GET) يُترك مفتوحًا عمدًا مثل
    # cards_checker v1 (في _NAV_VIEW_GUARD_SKIP) — أفعاله تُرسَل لمسار v1
    # المحروس بـcards.verify. لا نَحرس عرض الفاحص.
    "cards_batches_export_csv": "cards.view",
    "cards_batches_export_pdf": "cards.view",
    "cards_batches_export_xlsx": "cards.view",
    "distributors_list": "reports.finance",
    "connected_stats": "online.view",
    "connected_stats_json": "online.view",
    "diagnostics": "nas.view",
    "device_health_page": "nas.view",
    "device_health_api_checks": "nas.view",
    "device_health_api_list": "nas.view",
    "device_health_api_router_interfaces": "nas.view",
    "ipchange_page": "nas.view",
    "lifecycle_settings": "settings.view",
    # سلة المحذوفات (GET) — كانت بلا «حارس عرض» إطلاقًا فأيّ مسؤول مُسجَّل
    # (حتى viewer) يَصِلها بالعنوان ويَرى سجلّات محذوفة حسّاسة (مدراء/أدوار/
    # مشتركين). تُربط بـsettings.view مطابقةً لشقيقتها lifecycle_settings في
    # نفس شريط «البيانات والحفظ والأرشفة». الاستعادة (POST) محروسة بـ
    # cards.restore في _PERM_GUARDED. FLAG(audit 2026-06): قد يُفضّل المالك
    # رفعها (وlifecycle_settings) إلى super لمطابقة بند القسم backups=super.
    "recycle_bin": "settings.view",
    "integrations_hub": "settings.view",  # FLAG(QA): settings.view مبدئيّ
    "devices_new": "nas.create",
    "bw_new": "plans.create",
}


def _current_perms() -> frozenset[str]:
    """صلاحيات المسؤول الحالي — من الجلسة، مخزّنة لكل طلب في g."""
    cached = getattr(g, "_ui_perms", None)
    if cached is not None:
        return cached
    try:
        perms = frozenset(session.get("permissions") or ())
    except Exception:  # noqa: BLE001 — خارج سياق طلب (اختبارات/CLI)
        perms = frozenset()
    g._ui_perms = perms
    return perms


def can(perm_key: str | None) -> bool:
    """هل يملك المسؤول الحالي هذه الصلاحية؟ super_admin يمرّ دائمًا.

    نفس عقد الحارس الخادمي (_perm_guard في routes/blueprint.py):
      • perm_key فارغ/None        → True (بند غير محروس — مفتوح للجميع)؛
        يبسّط نمط القوالب can(perm_for_endpoint(ep)) بلا فحص إضافي.
      • session["is_super_admin"] → True دائمًا.
      • "__super__"               → super_admin فقط.
      • غير ذلك                   → المفتاح ضمن session["permissions"].
    """
    if not perm_key:
        return True
    try:
        if session.get("is_super_admin"):
            return True
    except Exception:  # noqa: BLE001
        return False
    if perm_key == _PERM_SUPER:
        return False
    return perm_key in _current_perms()


def ui_unauth_mode() -> str:
    """وضع عرض الأقسام غير المصرّح بها: "freeze" (تجميد بقفل) أو "hide"
    (إخفاء كلي) — من إعداد المالك security.unauthorized_ui، مخزّن لكل طلب."""
    cached = getattr(g, "_ui_unauth_mode", None)
    if cached is not None:
        return cached
    mode = UNAUTH_UI_DEFAULT
    try:
        from ..core.tenant import DEFAULT_TENANT_ID
        from ..db.repos import tenants_repo
        tid = int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
        val = (tenants_repo.get_setting(tid, UNAUTH_UI_SETTING_KEY,
                                        UNAUTH_UI_DEFAULT) or "").strip().lower()
        if val in _UNAUTH_UI_VALID:
            mode = val
    except Exception:  # noqa: BLE001 — لا يكسر أي صفحة أبدًا؛ الافتراضي تجميد
        mode = UNAUTH_UI_DEFAULT
    g._ui_unauth_mode = mode
    return mode


def perm_for_endpoint(endpoint: str) -> str | None:
    """مفتاح الصلاحية لبند تنقّل يشير إلى endpoint — أو None إن كان
    البند أساسيًا مفتوحًا للجميع (لوحة التحكم، مركز الأدلة...).

    يقبل الاسم مع البادئة radius. أو بدونها. يُحلّ أولًا من خريطة
    التنقّل _NAV_PERM (مفاتيح «العرض»)، ثم من حارس المسارات
    _PERM_GUARDED (مفاتيح الكتابة/التقارير) — كسول حتى لا تنشأ
    دورة استيراد مع routes/blueprint.py.
    """
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    hit = _NAV_PERM.get(name)
    if hit is not None:
        return hit
    try:
        from ..routes.blueprint import _PERM_GUARDED
        return _PERM_GUARDED.get(name)
    except Exception:  # noqa: BLE001
        return None


__all__ = ["can", "ui_unauth_mode", "perm_for_endpoint",
           "UNAUTH_UI_SETTING_KEY", "UNAUTH_UI_DEFAULT"]
