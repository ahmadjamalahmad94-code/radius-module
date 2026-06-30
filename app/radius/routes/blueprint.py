"""factory رئيسية لـ radius blueprint — يضم كل الأقسام + الـ auth + health/readiness."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, g, jsonify, request

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_PUBLIC_ENDPOINTS = {
    "radius.auth_login",
    "radius.auth_logout",
    "radius.portal_subscriber_login",
    "radius.portal_subscriber_logout",
    "radius.portal_subscriber_home",
    "radius.portal_subscriber_loan_request",
    "radius.portal_subscriber_renewal_request",
    "radius.portal_card_login",
    "radius.portal_card_logout",
    "radius.portal_card_home",
    "radius.portal_card_purchase",
    "radius._radius_health",
    "radius._radius_healthz",
    # Setup Wizard v3 — /wz/<short>.rsc must be reachable
    # from MikroTik /tool fetch without admin session cookies.
    # The secret short code in the URL path is the auth.
    "radius.setup_wizard_v3_serve_script",
    # System-health endpoint for external uptime monitoring.
    # Returns HTTP 200 when healthy, 503 when degraded/critical.
    # No secrets in the response — only check names + Arabic
    # titles + boolean-ish statuses.
    "radius.setup_wizard_system_health",
    # WhatsApp bot inbound webhook (Phase 2). Called server-to-server by the
    # WhatsApp gateway, which has no admin session cookie. It only reads the
    # incoming message and replies via the configured provider — never exposes
    # admin data — and always answers 200. Also CSRF-exempt (see app/__init__).
    "radius.communications_bot_webhook",
    # تحليلات صفحة الدخول: beacon من أجهزة الزبائن على الهوت سبوت بلا
    # جلسة إدارية. fail-open دائمًا (204) ولا يكشف أي بيانات إدارية.
    "radius.hotspot_analytics_collect",
    # نشر صفحة الدخول بلا FTP: الراوتر يسحب الملفات عبر /tool fetch خلال
    # النفق. الـtoken السرّي في المسار هو المصادقة (ولمرّة واحدة، TTL قصير).
    # لا كوكي إدارة للراوتر، فلا بدّ من الإعفاء من حارس الدخول.
    "radius.hotspot_publish_pull",
}


def get_radius_blueprint() -> Blueprint:
    bp = Blueprint(
        "radius", __name__,
        url_prefix="/admin/radius",
        template_folder=str(_TEMPLATES_DIR),
    )
    _register_health(bp)
    _register_auth(bp)
    _register_i18n(bp)
    _register_tenants(bp)
    _register_all(bp)
    _install_global_login_guard(bp)
    _install_permission_guard(bp)
    _install_error_handlers(bp)
    return bp


def _register_health(bp: Blueprint) -> None:

    @bp.get("/_health")
    def _radius_health():
        """liveness — لا يفحص شيئًا، فقط يردّ أنا حي. للـ docker/k8s liveness."""
        return jsonify({"module": "radius", "status": "ok"})

    @bp.get("/_healthz")
    def _radius_healthz():
        """readiness — يفحص DB + workers. status=503 إن degraded."""
        out = {"status": "ok", "checks": {}}
        try:
            from ..db.connection import db
            db().execute("SELECT 1").fetchone()
            out["checks"]["db"] = "ok"
        except Exception as e:  # noqa: BLE001
            out["checks"]["db"] = f"fail: {e}"
            out["status"] = "degraded"
        try:
            from app.workers.heartbeat import snapshot
            alive = [w for w in snapshot() if w["is_alive"]]
            out["checks"]["workers"] = {
                "alive": len(alive),
                "names": [w["name"] for w in alive],
            }
            if not alive and not _is_worker_disabled():
                out["status"] = "degraded"
        except Exception as e:  # noqa: BLE001
            out["checks"]["workers"] = f"fail: {e}"
        return jsonify(out), 200 if out["status"] == "ok" else 503


def _is_worker_disabled() -> bool:
    import os
    return bool(os.environ.get("HOBERADIUS_NO_WORKER"))


def _register_auth(bp: Blueprint) -> None:
    from .auth import register_auth_routes
    register_auth_routes(bp)


def _register_i18n(bp: Blueprint) -> None:
    from .i18n_routes import register_i18n_routes
    register_i18n_routes(bp)


def _register_tenants(bp: Blueprint) -> None:
    from .tenants import register_tenants_routes
    register_tenants_routes(bp)


def _register_all(bp: Blueprint) -> None:
    from .dashboard import register_dashboard_routes
    from .account import register_account_routes
    from .devices import register_devices_routes
    from .network_devices import register_network_devices_routes
    from .device_health import register_device_health_routes
    from .network_device_bypass import register_network_device_bypass_routes
    from .network_ip_scan import register_network_ip_scan_routes
    from .remote_device_access import register_remote_device_access_routes
    from .router_events import register_router_events_routes
    from .sessions import register_sessions_routes
    from .plans import register_plans_routes
    from .users import register_users_routes
    from .subscriber_groups import register_subscriber_groups_routes
    from .cards import register_cards_routes
    from .cards_print import register_cards_print_routes
    from .cards_recharge import register_cards_recharge_routes
    from .admins import register_admins_routes
    from .admin_pricing import register_admin_pricing_routes
    from .distributors import register_distributors_routes
    from .accounting import register_accounting_routes
    from .finance_center import register_finance_center_routes
    from .finance_center_hub import register_finance_center_hub_routes
    from .finance_accounting import register_finance_accounting_routes
    from .finance_billing import register_finance_billing_routes
    from .finance_collection import register_finance_collection_routes
    from .company_inventory import register_company_inventory_routes
    from .card_users_marketplace import register_card_users_marketplace_routes
    from .store_support import register_store_support_routes
    from .manager_distributor_ops import register_manager_distributor_ops_routes
    # لوحة الشحن — تفعيل/تجديد سريع للمدراء والموزعين (قراءات فقط؛
    # كل عمليات المال تمرّ عبر مسارات users/accounting القائمة).
    from .recharge_panel import register_recharge_panel_routes
    from .communications import register_communications_routes
    from .whatsapp import register_whatsapp_routes
    from .events_risk import register_events_risk_routes
    from .operations_center import register_operations_center_routes
    from .customer_portals import register_customer_portal_routes
    from .admin_bridge import register_admin_bridge_routes
    from .tunnels import register_tunnels_routes
    from .recycle_bin import register_recycle_bin_routes
    from .backups import register_backup_routes
    from .lifecycle import register_lifecycle_routes
    from .bandwidth_schedules import register_bandwidth_schedule_routes
    from .print_templates import register_print_template_routes
    from .payment_collection import register_payment_collection_routes
    # مختبر الدفع الإلكتروني — وضع تجريبي (محاكاة كاملة لتدفق الدفع)
    from .payments_lab import register_payments_lab_routes
    from .setup_wizard import register_setup_wizard_routes
    from .setup_wizard_v3 import (
        register_setup_wizard_v3_routes,
    )
    # نظام مواصفات الخدمات الموحّد — نافذة spec + نقاط تفعيل/ترقية.
    from .service_requests import register_service_requests_routes

    register_dashboard_routes(bp)
    register_account_routes(bp)
    from .notifications import register_notifications_routes
    register_notifications_routes(bp)
    # المجموعة الموحّدة «الإشعارات والتواصل» (Phase 1): التكاملات + إشعارات
    # الإدارة + إشعارات المشتركين (+ إعادة توجيه /network/telegram المكرّر).
    from .notification_hub import register_notification_hub_routes
    register_notification_hub_routes(bp)
    register_devices_routes(bp)
    from .mt_import import register_mt_import_routes
    register_mt_import_routes(bp)
    register_network_devices_routes(bp)
    register_device_health_routes(bp)
    register_network_device_bypass_routes(bp)
    register_network_ip_scan_routes(bp)
    register_remote_device_access_routes(bp)
    register_router_events_routes(bp)
    # network_telegram_settings (المكرّر) أُزيل — /network/telegram يُعاد توجيهه
    # إلى /integrations عبر notification_hub. الأساس القانوني هو tenant_telegram_settings.
    from .admin_alerts import register_admin_alerts_routes
    register_admin_alerts_routes(bp)
    register_sessions_routes(bp)
    register_plans_routes(bp)
    register_users_routes(bp)
    register_subscriber_groups_routes(bp)
    register_cards_routes(bp)
    register_cards_print_routes(bp)
    register_cards_recharge_routes(bp)
    register_admins_routes(bp)
    register_admin_pricing_routes(bp)
    register_distributors_routes(bp)
    register_accounting_routes(bp)
    register_finance_center_routes(bp)
    register_finance_center_hub_routes(bp)
    register_finance_accounting_routes(bp)
    register_finance_billing_routes(bp)
    register_finance_collection_routes(bp)
    register_company_inventory_routes(bp)
    register_card_users_marketplace_routes(bp)
    register_store_support_routes(bp)
    register_manager_distributor_ops_routes(bp)
    register_recharge_panel_routes(bp)
    register_communications_routes(bp)
    register_whatsapp_routes(bp)
    register_events_risk_routes(bp)
    register_operations_center_routes(bp)
    register_customer_portal_routes(bp)
    register_admin_bridge_routes(bp)
    register_tunnels_routes(bp)
    register_recycle_bin_routes(bp)
    register_backup_routes(bp)
    register_lifecycle_routes(bp)
    register_bandwidth_schedule_routes(bp)
    register_print_template_routes(bp)
    register_payment_collection_routes(bp)
    register_payments_lab_routes(bp)
    register_setup_wizard_routes(bp)
    register_setup_wizard_v3_routes(bp)
    register_service_requests_routes(bp)
    # خدمة «تغيير الـIP» المدفوعة — صفحة العميل (المرحلة 1).
    from .ipchange import register_ipchange_routes
    register_ipchange_routes(bp)

    from .saas_modules import register_saas_routes
    register_saas_routes(bp)

    from .integrations import register_integration_routes
    register_integration_routes(bp)

    from .mt_dashboard import register_mt_dashboard_routes
    register_mt_dashboard_routes(bp)

    from .mt_programming import register_mt_programming_routes
    register_mt_programming_routes(bp)

    from .port_script_services import register_port_script_services_routes
    register_port_script_services_routes(bp)

    from .mt_login_designer import register_mt_login_designer_routes
    register_mt_login_designer_routes(bp)

    from .hotspot_errors import register_hotspot_errors_routes
    register_hotspot_errors_routes(bp)

    # واجهة إدارة أعلام الأقسام (إخفاء/تعطيل قسم بكامله) — super_admin فقط
    from .sections_admin import register_sections_admin_routes
    register_sections_admin_routes(bp)

    from .jobs import register_jobs_routes
    register_jobs_routes(bp)

    from .audit_log import register_audit_log_routes
    register_audit_log_routes(bp)

    from .mt_topology import register_mt_topology_routes
    register_mt_topology_routes(bp)

    from .mt_alerts import register_mt_alerts_routes
    register_mt_alerts_routes(bp)

    from .mt_backups import register_mt_backups_routes
    register_mt_backups_routes(bp)

    from .mt_router_overview import register_mt_router_overview_routes
    register_mt_router_overview_routes(bp)

    from .mt_problems import register_mt_problems_routes
    register_mt_problems_routes(bp)

    from .mt_audit_timeline import register_mt_audit_timeline_routes
    register_mt_audit_timeline_routes(bp)

    from .mt_recovery_plan import register_mt_recovery_plan_routes
    register_mt_recovery_plan_routes(bp)

    from .mt_setup import register_mt_setup_routes
    register_mt_setup_routes(bp)

    from .mt_permission_matrix import (
        register_mt_permission_matrix_routes,
    )
    register_mt_permission_matrix_routes(bp)

    from .mt_guided_op import register_mt_guided_op_routes
    register_mt_guided_op_routes(bp)

    from .site_exit import register_site_exit_routes
    register_site_exit_routes(bp)

    from .network_policy import register_network_policy_routes
    register_network_policy_routes(bp)

    from .tokens import register_tokens_routes
    register_tokens_routes(bp)

    from .status import register_status_routes
    register_status_routes(bp)

    from .reports import register_reports_routes
    register_reports_routes(bp)
    from .connected_stats import register_connected_stats_routes
    register_connected_stats_routes(bp)

    from .tools import register_tools_routes
    register_tools_routes(bp)

    from .settings import register_settings_routes
    register_settings_routes(bp)

    from .access_control import register_access_control_routes
    register_access_control_routes(bp)

    from .anti_mac_clone import register_anti_mac_clone_routes
    register_anti_mac_clone_routes(bp)

    from .system_settings import register_system_settings_routes
    register_system_settings_routes(bp)

    from .overviews import register_overview_routes
    register_overview_routes(bp)

    from .subscribers_overview import register_subscribers_overview_routes
    register_subscribers_overview_routes(bp)

    from .share_groups import register_share_groups_routes
    register_share_groups_routes(bp)

    # تصدير الجداول الموحّد (PDF/XLSX/CSV لأي جدول في الواجهة)
    from .table_export import register_table_export_routes
    register_table_export_routes(bp)

    # صفحات بوابة المزوّد (provider gate): «الخدمة موقوفة» + «حالة المنح».
    # تُسجَّل مبكرًا لضمان توفّر الـendpoint قبل أوّل redirect من الحارس.
    from .provider_gate_pages import register_provider_gate_pages
    register_provider_gate_pages(bp)

    # صفحات دورة حياة الترخيص: «فعّل الترخيص» (لم يُفعَّل) + «منتهٍ — جدّد».
    # تُسجَّل مبكرًا لضمان توفّر الـendpoint قبل أوّل redirect من حارس
    # _perm_guard على نسخ غير مفعّلة/منتهية.
    from .license_lifecycle_pages import register_license_lifecycle_pages
    register_license_lifecycle_pages(bp)

    # «ربط وتفعيل النسخة» — صفحة موحَّدة بسيطة تَستبدل التَعقيد القائم في
    # license_file + admin_bridge. زرّ واحد كبير «ربط وتفعيل الآن» +
    # «فكّ الربط» لإعادة الدورة. تُسجَّل مبكرًا كي يَنفذ الـredirect إليها
    # من شاشة activate قبل أن يَكتمل تركيب الحارس.
    from .activation_connect import register_activation_connect_routes
    register_activation_connect_routes(bp)

    # مركز الأدلة «كيف تستخدمني» — شروحات مصوّرة داخل الموقع
    from .docs_center import register_docs_center_routes
    register_docs_center_routes(bp)

    # حسابات VPN — Phase 6: تفعيل SSTP/L2TP ضمن حدود التخصيصات
    from .vpn_accounts import register_vpn_accounts_routes
    register_vpn_accounts_routes(bp)

    # WireGuard بيانات — Phase 7: إدارة wg-data (مستقل عن wg-mgmt)
    from .wg_data import register_wg_data_routes
    register_wg_data_routes(bp)

    # لوحة المتابعة — Phase 8: مقاييس الصحة وتنبيهات السعة
    from .monitoring import register_monitoring_routes
    register_monitoring_routes(bp)

    # تقارير VPN + WireGuard + سجل التدقيق — Phase 10: تصدير CSV
    from .vpn_reports import register_vpn_reports_routes
    register_vpn_reports_routes(bp)

    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كانت هنا «لوحة التراخيص — خدمة نفق تغيير IP المدفوعة» (licensing.py).
    # حوكمة مركزية للمالك، لا تخص لوحة العميل المباعة.


# صفحات يُسمح بها رغم وجوب تغيير كلمة المرور عند أول دخول — أضيق ما يلزم كي
# يستطيع الأدمن المُنشأ مركزياً تغيير كلمته أو الخروج دون حلقة إعادة توجيه.
_MUST_CHANGE_ALLOWED: frozenset[str] = frozenset({
    "radius.account",
    "radius.account_password",
    "radius.auth_logout",
    "radius.auth_login",
    "radius.set_locale",
})


def _install_global_login_guard(bp: Blueprint) -> None:
    """يحرس كل الـ endpoints الإدارية بـ login، عدا public."""
    from ..auth.session_helpers import current_admin_id

    @bp.before_request
    def _guard():
        # السماح بكل الـ public + كل ما يخص الـ static
        ep = request.endpoint or ""
        if ep in _PUBLIC_ENDPOINTS:
            return None
        # حمى: غير مسجَّل = إعادة توجيه لـ login
        if not current_admin_id():
            from flask import redirect, url_for, flash
            flash("سجّل الدخول للمتابعة.", "warning")
            return redirect(url_for("radius.auth_login", next=request.path))
        # إلزام تغيير كلمة المرور عند أول دخول: الأدمن الذي أنشأته لوحة التراخيص
        # مركزياً بكلمة مرور أوليّة يُحوَّل لصفحة الحساب حتى يغيّرها — تُستثنى صفحة
        # الحساب نفسها + الخروج + مبدّل اللغة كي لا تحدث حلقة إعادة توجيه.
        if ep not in _MUST_CHANGE_ALLOWED:
            from ..auth.session_helpers import current_admin
            _a = current_admin()
            if _a is not None and getattr(_a, "must_change_password", False):
                from flask import redirect, url_for, flash
                flash("لأمانك، يجب تغيير كلمة المرور قبل المتابعة.", "warning")
                return redirect(url_for("radius.account"))
        return None


# صفحات تبقى مكشوفة لحارس مُنحة المزوّد (provider gate) — حتى لو أوقف المزوّد
# كل الخدمات، تظلّ هذه الصفحات قابلة للوصول كي يرى المسؤول ما الذي حدث ويتصل
# بالمزوّد. مفاتيح بلا بادئة 'radius.'.
_PROVIDER_GATE_SKIP: frozenset[str] = frozenset({
    "provider_blocked_page",       # صفحة «الخدمة غير مفعّلة من المزوّد»
    "provider_grants_status_page", # حالة المنح من المزوّد (تشخيصية)
    "provider_upgrade_page",       # صفحة «طلب تفعيل / ترقية» (locked_upgrade)
    "license_activate_page",       # «فعّل الترخيص» (لم يُفعَّل)
    "license_expired_page",        # «الترخيص منتهي — جدّد»
    # «ربط وتفعيل» الموحّد + actions (link/reset/sync-now) لا تُحجَب
    "license_connect_page",
    "license_connect_link",
    "license_connect_sync_now",
    "license_connect_reset",
    "auth_login", "auth_logout",   # ضرورية للدخول/الخروج
    "set_locale",                  # مبدّل اللغة
    "license_file",                # صفحة الترخيص (تعرض حالة الاتصال بالمزوّد)
    "admin_bridge",                # جسر التكامل (لإعادة المزامنة)
    "dashboard",                   # تبقى قابلة للوصول للقراءة (بدون إجراءات)
})

# صفحات تبقى مكشوفة لحارس دورة حياة الترخيص (license lifecycle gate) —
# مجموعة أضيق: فقط ما يحتاجه المسؤول للإصلاح/الخروج/التشخيص. لا نُضيف
# dashboard هنا لأنّ ترخيصًا منتهيًا/غير مفعَّل = اللوحة كاملة مقفلة بما
# فيها الصفحة الرئيسية. الفرق عن provider gate: هذا أعلى سلطة وأقفل أوسع.
_LIFECYCLE_GATE_SKIP: frozenset[str] = frozenset({
    "license_activate_page",
    "license_expired_page",
    "provider_grants_status_page", # تشخيصي — مفيد لرؤية اللقطة
    "provider_blocked_page",       # قد يصل إليها من رابط قديم
    "provider_upgrade_page",       # locked_upgrade page reachable من رابط قديم
    # «ربط وتفعيل» الموحّد — أهم صفحة على نسخة مقفلة، أكشن link الفعلي
    # الذي يُفعِّلها، sync-now للمحاولة الثانية، reset للاختبار.
    "license_connect_page",
    "license_connect_link",
    "license_connect_sync_now",
    "license_connect_reset",
    "auth_login", "auth_logout",
    "set_locale",
    "license_file",
    "admin_bridge",
})


# الحارس الثاني: صلاحيات الدور (RBAC) على المسارات الحسّاسة.
# super_admin يمرّ دائمًا؛ غيره يُمنع (403) من إدارة المسؤولين/الأدوار/الـ tenants،
# والنسخ الاحتياطي (استرجاع/حذف/تنزيل)، وعكس قيود المحاسبة، وحفظ إعدادات النظام.
_PERM_SUPER = "__super__"
_PERM_GUARDED: dict[str, str] = {
    # إدارة المسؤولين والأدوار — سطح تصعيد الصلاحيات (super_admin فقط)
    "admins_create": _PERM_SUPER, "admins_update": _PERM_SUPER, "admins_delete": _PERM_SUPER,
    "roles_create": _PERM_SUPER, "roles_update": _PERM_SUPER, "roles_save": _PERM_SUPER,
    "roles_delete": _PERM_SUPER,
    # إدارة الـ tenants (super_admin فقط)
    "tenants_create": _PERM_SUPER, "tenants_update": _PERM_SUPER,
    # «فتح WinBox» — وصول بعيد لإدارة الراوتر عبر منفذ عام. سطح حسّاس
    # (يكشف WinBox مؤقّتًا) → super_admin فقط على كل الـmethods (GET/POST).
    "mt_remote_winbox_open": _PERM_SUPER,
    "mt_remote_close": _PERM_SUPER,
    "mt_remote_sessions": _PERM_SUPER,
    # ── صفحات «المزوّد فقط» — تنظيف لوحة العميل (chore يونيو 2026) ──
    # العميل مستأجر واحد؛ إدارة الجهات المتعدّدة (قائمة/إضافة) والتحصيل
    # المجمّد ومختبر الدفع التجريبي يتحكّم بها المزوّد من لوحة التراخيص.
    # أُزيلت روابطها من الشريط الجانبي، وتُحرَس مساراتها هنا بمستوى المسار:
    # super_admin (ومنه المدير الرئيسي) فقط. ليست في _PERM_WRITE_ONLY فيسري
    # الحارس على كل الـmethods (GET/POST) → غير السوبر 403. المسارات تبقى
    # مسجّلة كي يَصِلها المالك (تعديل ملف مستأجره/الإطلاع) بلا BuildError.
    # tenants_list/new/edit صفحات GET — كانت تُفلت من حارس العرض لأنّها لم
    # تَكن في _NAV_PERM؛ تسجيلها هنا يُغلق ثغرة الوصول المباشر بالعنوان.
    "tenants_list": _PERM_SUPER, "tenants_new": _PERM_SUPER, "tenants_edit": _PERM_SUPER,
    # التحصيل والمدفوعات — مجمّد؛ يُعاد مركزياً عبر لوحة التراخيص.
    "collection_hub": _PERM_SUPER,
    # مختبر الدفع الإلكتروني — تجريبي (WIP)؛ مخفيّ تمامًا من الشريط الجانبي.
    "payments_lab": _PERM_SUPER, "payments_lab_webhook": _PERM_SUPER,
    # النسخ الاحتياطي: عمليات مدمّرة أو تسريب بيانات (super_admin فقط)
    "backups_run": _PERM_SUPER, "backups_run_all": _PERM_SUPER, "backups_restore": _PERM_SUPER,
    "backups_delete": _PERM_SUPER, "backups_schedule": _PERM_SUPER, "backups_settings": _PERM_SUPER,
    "backups_upload_computer": _PERM_SUPER, "backups_upload_panel": _PERM_SUPER,
    "backups_download": _PERM_SUPER, "backups_content": _PERM_SUPER,
    "backups_gdrive_save": _PERM_SUPER, "backups_gdrive_start": _PERM_SUPER,
    "backups_gdrive_disconnect": _PERM_SUPER,
    # عكس قيد محاسبي (مدمّر) — super_admin فقط
    "finance_ledger_void": _PERM_SUPER,
    # «الإعداد الهندسي» (setup_wizard_page) — مخفي مؤقتاً بطلب المالك:
    # أزيل رابطه من شريط إدارة الراوترات (network_ops_nav.html)، والمسار
    # /admin/radius/setup-wizard يبقى مسجّلًا لكنه super_admin فقط.
    "setup_wizard_page": _PERM_SUPER,
    # ── واجهة إدارة أعلام الأقسام (إخفاء/تعطيل قسم بكامله) — super_admin فقط ──
    # حتى لا يُعطّل غير السوبر القائمةَ على نفسه.
    "sections_admin_page":  _PERM_SUPER,
    "sections_admin_save":  _PERM_SUPER,
    "sections_admin_reset": _PERM_SUPER,
    # ── «ربط وتفعيل النسخة» — مفاتيح ربط/فكّ ربط النسخة مع لوحة المزوّد:
    # super_admin فقط (قرار تجاري + تخريبي عند الخطأ — يَمسح التزامن). ──
    "license_connect_link":     _PERM_SUPER,
    "license_connect_reset":    _PERM_SUPER,
    "license_connect_sync_now": _PERM_SUPER,
    # حفظ إعدادات النظام — تتطلّب صلاحية settings.edit (تُفحص على الكتابة فقط)
    "settings_page": "settings.edit",
    # «الحظر والتحكم بالدخول» — كتابة محروسة بـsettings.edit (العرض بـsettings.view)
    "access_control_save_settings": "settings.edit",
    "access_control_add_block": "settings.edit",
    "access_control_clear_block": "settings.edit",
    # «منع استنساخ MAC» — كتابة محروسة بـsettings.edit (نفس نمط access_control)
    "anti_mac_clone_save_settings":  "settings.edit",
    "anti_mac_clone_binding_action": "settings.edit",
    # «نمط السماح» — كتابة محروسة بنفس مفتاح صلاحية القسم.
    "access_control_allow_mode_upsert":         "settings.edit",
    "access_control_allow_mode_delete_policy":  "settings.edit",
    "access_control_allow_mode_toggle_policy":  "settings.edit",
    "access_control_allow_mode_add_device":     "settings.edit",
    "access_control_allow_mode_delete_device":  "settings.edit",
    # الأنفاق — طلب/مزامنة نفق عبر الجسر (كتابة) تتطلّب api.use
    "tunnels_request": "api.use",
    "tunnels_sync": "api.use",

    # ═══ أسعار العروض للمدراء — مفاتيح دقيقة بدل super_admin (توسعة 2026-06) ═══
    "admin_pricing_page": "admin_pricing.view",
    "admin_pricing_save": "admin_pricing.edit",
    "admin_pricing_reset": "admin_pricing.reset",
    "admin_pricing_reset_all": "admin_pricing.reset",

    # ═══ المستفيدون — عمليات تشغيلية دقيقة (routes/users.py) ═══
    "users_toggle": "users.change_status", "users_toggle_bulk": "users.change_status",
    "users_extend": "users.extend", "users_extend_bulk": "users.extend",
    "users_change_plan": "users.change_plan",
    "users_quota_topup": "users.quota", "users_quota_topup_bulk": "users.quota",
    "users_quota_reset_daily": "users.quota", "users_quota_reset_daily_bulk": "users.quota",
    "users_balance_add": "users.balance_add", "users_balance_add_bulk": "users.balance_add",
    "users_send_sms": "users.send_message", "users_send_sms_bulk": "users.send_message",
    # السرعة المؤقتة — مفتاحها الخاص (كانت users.edit قبل التوسعة؛
    # الترحيل 099 يمنح users.temp_speed لكل دور يملك users.edit)
    "online_temp_speed": "users.temp_speed",
    "online_temp_speed_cancel": "users.temp_speed",
    "users_temp_speed_cancel": "users.temp_speed",
    # دفعات/سلف المشتركين (routes/accounting.py)
    "users_payment_create": "users.payments", "users_payment_create_bulk": "users.payments",
    "users_loan_create": "users.loans", "users_loan_create_bulk": "users.loans",
    "users_loan_settle": "users.loans",
    # التصدير الموحّد لكل الجداول — endpoint واحد يخدم كل الشاشات،
    # نحرسه بأقرب مفتاح (users.export) لأن أغلب الجداول بيانات مشتركين.
    "export_table": "users.export",
    # حذف المشتركين الجماعي — نفس مفتاح الحذف الفردي
    "users_bulk_delete": "users.delete",

    # ═══ المتصلون الآن (routes/sessions.py) ═══
    "online_list": "online.view",
    "online_live_status": "online.view",
    "online_reconcile": "online.disconnect",
    "online_disconnect": "online.disconnect",
    "online_lock_mac": "online.lock_mac",
    "online_lock_ip": "online.lock_ip",

    # ═══ البطاقات — عمليات دقيقة (cards.py + recharge + print + recycle) ═══
    "cards_batch_edit": "cards.edit_batch",
    "cards_batches_bulk": "cards.batch_ops",
    "cards_batch_cards_actions": "cards.batch_ops",
    "cards_batches_import": "cards.import",
    "cards_batches_import_preview": "cards.import",
    # فاحص البطاقات: POST واحد (cards_checker) يتفرّع داخليًا إلى عدة
    # عمليات (تفعيل/تعطيل/تثبيت MAC/تعديل وقت/سرعة/حذف ناعم) — نحرسه
    # عند مستوى المسار بأقرب مفتاح cards.verify (الـ GET للعرض لا يُحجب،
    # انظر استثناء _perm_guard أدناه).
    "cards_checker": "cards.verify",
    # البحث في الفاحص قراءة فقط (GET JSON) — يكفي مفتاح عرض البطاقات
    "cards_checker_api_lookup": "cards.view",
    # كشف كلمة سر البطاقة — مفتاح إشرافي مستقل (scope.view_passwords)
    "cards_checker_api_reveal_password": "scope.view_passwords",
    "cards_generate": "cards.generate",
    "cards_generate_progress_start": "cards.generate",
    "cards_revoke": "cards.revoke",
    # سلة المحذوفات: الاستعادة تخص البطاقات المحذوفة
    "recycle_bin_restore": "cards.restore",
    # بطاقات الشحن وحزم الطباعة
    "cards_recharge_new": "cards.recharge",
    "cards_recharge_batch_delete": "cards.recharge",
    "cards_print_new": "cards.print",
    "cards_print_batch_delete": "cards.print",

    # ═══ الباقات (routes/plans.py) ═══
    "plans_create": "plans.create",
    "plans_update": "plans.edit",
    "plans_delete": "plans.delete",

    # ═══ أجهزة الشبكة — NAS/MikroTik (devices.py + network_devices.py) ═══
    "devices_create": "nas.create",
    "devices_update": "nas.edit", "devices_toggle": "nas.edit", "devices_bulk_toggle": "nas.edit",
    "devices_delete": "nas.delete",
    "network_devices_create": "nas.create",
    "network_devices_update": "nas.edit",
    "network_devices_delete": "nas.delete",

    # ═══ محافظ المشغّلين — شحن رصيد مدير/موزع وضبط سياساته ═══
    "business_operator_recharge": "admins.deposit_balance",
    "business_operator_policy": "admins.policy",

    # ═══ التقارير (routes/reports.py + accounting.py) ═══
    "reports_home": "reports.view", "reports_financial": "reports.view",
    "reports_cards": "reports.view", "reports_distributors": "reports.view",
    "reports_archive": "reports.view", "reports_archive_create": "reports.view",
    "rep_sessions": "reports.view", "rep_failed_logins": "reports.view",
    "rep_subscriber_consumption": "reports.view",
    "rep_login_status": "reports.view", "rep_login_states": "reports.view",
    "rep_login_states_cards": "reports.view", "rep_login_states_subscribers": "reports.view",
    "rep_login_states_sub_portal": "reports.view",
    "rep_login_states_card_store": "reports.view",
    "rep_login_states_admin": "reports.view",
    "rep_mac_history": "reports.view", "rep_profile_changes": "reports.view",
    "rep_api_messages": "reports.view", "rep_coa_failures": "reports.view",
    "rep_manager_events": "reports.view", "rep_manager_login_status": "reports.view",
    "rep_user_events": "reports.view", "rep_speed_failures": "reports.view",
    "rep_used_cards": "reports.view", "rep_balance_movements": "reports.view",
    "rep_cash_transactions": "reports.view",
    # دفتر القيود والتقارير المالية — مفتاح مالي مستقل
    "finance_ledger": "reports.finance",
    "finance_reports": "reports.finance",
    "finance_reports_snapshot": "reports.finance",
    "finance_reports_export_csv": "reports.finance",
    "finance_reports_export_xlsx": "reports.finance",
    "finance_reports_export_pdf": "reports.finance",

    # ═══ لوحة دعم المتجر المتقدّم (routes/store_support.py) ═══
    # «المدير يشيك»: تأكيد الإيداع يضيف الرصيد وتأكيد السحب يخصمه —
    # حركة مال حقيقية. تُقصر كل اللوحة (العرض + التأكيد/الرفض + القنوات
    # + الشات) على من يملك store.review؛ super_admin يتجاوز دائمًا.
    "store_support": "store.review",
    "store_support_deposit_confirm": "store.review",
    "store_support_deposit_reject": "store.review",
    "store_support_withdrawal_confirm": "store.review",
    "store_support_withdrawal_reject": "store.review",
    "store_support_payment_method_create": "store.review",
    "store_support_payment_method_update": "store.review",
    "store_support_chat_post": "store.review",

    # ═══════════════════════════════════════════════════════════════════
    # ═══ تدقيق QA RBAC (2026-06): سدّ «الشريط يُخفي والعنوان يَصِل» ═══
    # مسارات كتابة/إجراء حسّاسة كانت بلا أيّ حارس (لا هنا ولا في _NAV_PERM
    # ولا requires_perm ولا section_flags) → أيّ مسؤول مُسجَّل (حتى viewer
    # بصلاحية واحدة) كان يُنفّذها مباشرةً بالعنوان. تُربط الآن بمفاتيحها.
    # المفاتيح موجودة سلفًا؛ super_admin/المدير الرئيسي يتجاوز دائمًا.
    # ═══════════════════════════════════════════════════════════════════

    # ── مالية: محافظ Business OS (إضافة/خصم رصيد = حركة مال حقيقيّة) ──
    # كانت أخطر ثغرة ماليّة: viewer يَقدر يَشحن/يَخصم أيّ محفظة بلا حدّ.
    "business_finance_wallets_create": "reports.finance",
    "business_finance_wallet_credit": "reports.finance",
    "business_finance_wallet_debit": "reports.finance",
    # ── الموزّعون/المناديب — تسوية دَيْن/تخصيص دفعات/إنشاء/تعديل = حركة مال ──
    "distributors_create": "reports.finance",
    "distributors_update": "reports.finance",
    "distributors_assign_batch": "reports.finance",
    "distributors_settle": "reports.finance",
    # ── الفواتير + جرد الشركة والمصروفات — ماليّة ──
    "inv_create": "reports.finance",
    "inv_status": "reports.finance",
    "company_expense_add": "reports.finance",
    "company_inventory_incoming": "reports.finance",
    "company_inventory_item_create": "reports.finance",
    "company_inventory_item_deactivate": "reports.finance",
    "company_inventory_usage": "reports.finance",
    # ── تحصيل المدفوعات — مزوّد فقط (مطابقة collection_hub = super) ──
    "payment_collection_approve_web": _PERM_SUPER,
    "payment_collection_reject_web": _PERM_SUPER,
    "payment_collection_apply_service_web": _PERM_SUPER,
    "payment_collection_settings": _PERM_SUPER,

    # ── متجر الكروت الإلكترونيّة (إدارة العملاء/الشحن/الشراء/كتالوج الباقات) ──
    # FLAG(QA): لا مفتاح مخصّص «cards.store»؛ نربطها بـcards.recharge (أقرب
    # مفتاح مالي للكروت). يُنصَح لاحقًا بمفتاح مستقلّ — راجع تقرير QA.
    "card_users_create": "cards.recharge",
    "card_user_password": "cards.recharge",
    "card_user_recharge": "cards.recharge",
    "card_user_purchase": "cards.recharge",
    "card_marketplace_package_create": "cards.recharge",
    "card_marketplace_package_mode": "cards.recharge",
    "card_marketplace_inventory_upload": "cards.recharge",
    "card_marketplace_default_mode": "cards.recharge",

    # ── قوالب الطباعة — كتابة بمفتاح طباعة الكروت ──
    "print_templates_create": "cards.print",
    "print_templates_update": "cards.print",
    "print_templates_delete": "cards.print",
    "print_templates_set_default": "cards.print",
    "print_templates_preview": "cards.print",
    "print_templates_designer_svg": "cards.print",
    "print_templates_export_job_start": "cards.print",
    "print_templates_cleanup_fixtures": "cards.print",
    "cards_print_new_preview": "cards.print",

    # ── الباقات/السرعات (bandwidth profiles + schedules) ──
    "bw_create": "plans.create",
    "bw_update": "plans.edit",
    "bw_delete": "plans.delete",
    "bw_apply": "plans.edit",
    "bandwidth_schedules_create": "plans.edit",
    "bandwidth_schedules_update": "plans.edit",
    "bandwidth_schedules_delete": "plans.delete",
    "bandwidth_schedules_apply": "plans.edit",

    # ── مجمّعات العناوين (IP pools) — بنية شبكيّة (تعديل الراوترات) ──
    "pool_create": "nas.edit",
    "pool_update": "nas.edit",
    "pool_delete": "nas.edit",

    # ── راوترات MikroTik — CRUD + إعداد + استيراد + اختبار (لم تكن محروسة) ──
    "mt_create": "nas.create",
    "mt_update": "nas.edit",
    "mt_delete": "nas.delete",
    "mt_test": "nas.view",
    "mt_setup_create": "nas.create",
    "mt_service_request": "nas.edit",
    "mt_import_preview": "nas.create",
    "mt_import_run": "nas.create",
    "devices_test": "nas.view",
    # تشغيل المُصالِح يدويًّا (flush ghost sessions) — حدّ أدنى: عرض الشبكة.
    "reconcile_now": "nas.view",
    # جامع تحليلات الهوتسبوت (POST) — عرض الشبكة.
    "hotspot_analytics_collect": "nas.view",

    # ── صحّة الأجهزة (device-health) — كل الكتابات بمفتاح تعديل الشبكة ──
    "device_health_api_poll_settings": "nas.edit",
    "device_health_api_live_apply": "nas.edit",
    "device_health_api_create": "nas.edit",
    "device_health_api_update": "nas.edit",
    "device_health_api_delete": "nas.edit",
    "device_health_api_enable": "nas.edit",
    "device_health_api_disable": "nas.edit",
    "device_health_api_apply": "nas.edit",
    "device_health_api_sync": "nas.edit",
    "device_health_api_test_ping": "nas.edit",
    "device_health_api_poll": "nas.edit",
    "device_health_api_poll_stream": "nas.edit",
    "device_health_api_plan": "nas.edit",

    # ── أجهزة الشبكة (network/*) — تجاوز/فحص/مسح = تعديل الشبكة ──
    "network_device_bypass_apply": "nas.edit",
    "network_device_bypass_remove": "nas.edit",
    "network_devices_check": "nas.edit",
    "network_ip_scan_add": "nas.edit",
    # وصول بعيد لأجهزة الشبكة — حسّاس مثل mt_remote (super فقط).
    "remote_device_access_open": _PERM_SUPER,
    "remote_device_access_close": _PERM_SUPER,

    # ── CoA الحيّ (تغيير IP/سرعة جلسة قائمة) ──
    "online_coa_set_ip": "online.lock_ip",
    "online_coa_set_speed": "users.temp_speed",

    # ── الاتصالات/الرسائل — كل الصفحات الفرعيّة + الإجراءات بمفتاح الإرسال ──
    "communications_audience": "users.send_message",
    "communications_bot": "users.send_message",
    "communications_campaigns": "users.send_message",
    "communications_channels": "users.send_message",
    "communications_channels_test": "users.send_message",
    "communications_deliveries": "users.send_message",
    "communications_guide": "users.send_message",
    "communications_notifications": "users.send_message",
    "communications_quota": "users.send_message",
    "communications_quota_credit": "users.send_message",
    "communications_quota_request": "users.send_message",
    "communications_send": "users.send_message",
    "communications_templates": "users.send_message",

    # ── مركز الأحداث (الصفحات الفرعيّة GET/POST) — مفتاح عرض السجلّ ──
    "events_investigations": "audit.view",
    "events_risk": "audit.view",
    "events_security": "audit.view",

    # ── دورة الحياة/الاحتفاظ (lifecycle) — تشغيل/سياسات = حذف بيانات مجدول ──
    # FLAG(QA): قد يُفضّل المالك قصرها على super؛ المبدئيّ settings.edit.
    "lifecycle_run": "settings.edit",
    "lifecycle_policy_create": "settings.edit",
    "lifecycle_policy_disable": "settings.edit",

    # ── تنبيهات تيليجرام للمسؤول (إعداد البوت/التفعيل/الاختبار) ──
    # FLAG(QA): مفتاح settings.edit مبدئيّ (إعداد على مستوى النظام).
    "admin_alerts_save_bot": "settings.edit",
    "admin_alerts_toggle": "settings.edit",
    "admin_alerts_test": "settings.edit",
    "admin_alerts_test_connection": "settings.edit",
    "admin_alerts_connect_start": "settings.edit",
    "admin_alerts_connect_poll": "settings.edit",

    # ── تغيير IP العام (دفع السكربت) + ملف الترخيص (إعداد/مزامنة/طلب خدمة) ──
    "ipchange_push": "nas.edit",
    "license_file_config": "api.use",
    "license_file_sync": "api.use",
    "license_file_service_request": "api.use",
}

# مسارات GET+POST معًا: نحرس الكتابة (POST) فقط ونترك العرض —
# settings_page تعرض صفحة الإعدادات، و cards_checker يعرض الفاحص
# و cards_generate يعرض نموذج التوليد و cards_batches_import يعرض
# نموذج الاستيراد و cards_recharge_new/cards_print_new نماذج الإنشاء.
_PERM_WRITE_ONLY = {
    "settings_page", "cards_checker", "cards_generate",
    "cards_batches_import", "cards_recharge_new", "cards_print_new",
    "cards_batch_edit",
}

# بنود تنقّل (sidebar) لها حارسها الخاص أو يُترك عرضها مفتوحًا عمدًا —
# لا يُطبَّق عليها «حارس العرض» المركزي المشتقّ من خريطة _NAV_PERM:
#   • audit_log_index/mt_alerts_index/hotspot_errors_page محروسة أصلًا
#     بديكوريتر requires_perm (نطاق mikrotik.* + صفحة 403 معرّبة خاصة)؛
#     الحارس المركزي يقطع قبلها بصفحة 403 افتراضية فيكسر تجربتها.
#   • cards_checker: عرض الفاحص (GET) مفتوح عمدًا، والـPOST محروس
#     بـcards.verify (انظر _PERM_WRITE_ONLY أعلاه).
_NAV_VIEW_GUARD_SKIP = {
    "audit_log_index", "mt_alerts_index", "hotspot_errors_page",
    "cards_checker",
}


def _install_permission_guard(bp: Blueprint) -> None:
    """RBAC server-side: يمنع الأدوار المحدودة من المسارات الحسّاسة.

    ثلاثة حُرّاس متكاملون في دالة before_request واحدة:

      (1) حارس الكتابة/السوبر (_PERM_GUARDED): يفحص مفاتيح الكتابة
          (POST/…)‎ + المناطق super_admin-only. كما كان.

      (2) حارس العرض (sidebar parity): كل بند تنقّل في خريطة _NAV_PERM
          يفرض مفتاح «عرضه» خادميًا على القراءة (GET) — فلا يكفي أن
          يُخفيه الشريط الجانبي، بل يُمنع الوصول المباشر بالعنوان (403).
          بهذا تتطابق خريطة الشريط الجانبي مع حراسة المسار فعليًا
          (إصلاح Broken Access Control: «الراوتر يحمي، لا الواجهة فقط»).

      (3) حارس أعلام القسم (section flags): سجلّ مركزي يربط كل قسم
          بـendpointاته ويحمل علمَي «إخفاء/تعطيل» قابلين للقلب من
          واجهة الإدارة. يُغلق الـendpointات التي خرجت من الشريط
          الجانبي لكنّ مساراتها بقيت مفتوحة (شبكة العمليات التجريبية،
          دفع بيانات التوزيع، الإعداد الهندسي، إعداد الأسطول…) ـ
          فيُعيد 403 على أيّ method لمن ليس سوبر.

    **المالك الرئيسي وحده** (الحساب الجذر، ``primary_admin_id()``) يتجاوز
    الحُرّاس الثلاثة دائمًا — وهو ما تَحمله الجلسة في
    ``session["is_super_admin"]`` (راجع ``session_helpers._resolve_is_super``
    بعد قرار المالك: العلم وحده/دور super_admin المُسنَد لم يَعُد يَمنح
    التجاوز). حامل دور super_admin يَمرّ عبر فحوصات الصلاحيات أدناه
    كأيّ مدير (403 عند غياب الصلاحية المطلوبة).
    """
    from flask import session, abort
    # خريطة عرض الشريط الجانبي — مصدر واحد لربط endpoint بمفتاح عرضه.
    # تُستورد مرّة عند تركيب الحارس (لا دورة استيراد: ui_permissions لا
    # يستورد blueprint إلا كسولًا داخل perm_for_endpoint وقت الطلب).
    from ..auth.ui_permissions import _NAV_PERM
    # سجلّ أعلام الأقسام — تُقرأ كلّها مرّة لكل طلب عبر flask.g (راجع
    # section_flags.is_section_blocked). لا استيرادات حلقية.
    from ..auth.section_flags import is_section_blocked

    @bp.before_request
    def _perm_guard():
        ep = request.endpoint or ""
        if not ep.startswith("radius."):
            return None
        name = ep.split(".", 1)[1]
        is_super = bool(session.get("is_super_admin"))
        perms = session.get("permissions") or []

        # ── (0) حارس مُنحة المزوّد — أعلى سلطة، فوق السوبر-أدمن. ──
        # المزوّد (لوحة التراخيص) يبيع الخدمات للعميل؛ إيقافها/إخفاؤها يسري
        # على كل الريديوس بما في ذلك المدير الرئيسي. هذا قرار تجاري، ليس
        # RBAC داخلي — السوبر يتجاوز RBAC لكن لا يتجاوز عقد المزوّد. الحارس
        # هنا لا يستثني السوبر عمدًا.
        # نقاط الإدارة التشخيصية لصفحة حالة المنح + اللوحات التشخيصية
        # الأساسية + login/logout تبقى مكشوفة كي يظلّ المسؤول قادرًا على
        # رؤية وضع المزوّد حتى لو أوقف خدمة كاملة.
        # ── (0a) حارس دورة حياة الترخيص — أعلى سلطة على الإطلاق. ──
        # يفصل بين «انقطاع تزامن عابر» (fail-open، الإبقاء على آخر معلوم
        # ضمن السماحية المحلّية) و«ترخيص منتهٍ/غير مفعّل» (fail-closed،
        # إقفال شامل). لا تخطّي من السوبر-أدمن — قرار تجاري بين العميل
        # والمزوّد فوق RBAC الداخلي.
        # المجموعة _LIFECYCLE_GATE_SKIP ضيّقة عمدًا: فقط ما يحتاجه المسؤول
        # للإصلاح/الخروج/التشخيص (lockout pages + login/logout + locale +
        # ترخيص + جسر + حالة المنح).
        if name not in _LIFECYCLE_GATE_SKIP:
            try:
                from ..services.license_lifecycle import (
                    evaluate_cached, LifecycleState)
                from ..core.tenant import DEFAULT_TENANT_ID
                tid = int(getattr(g, "tenant_id", None)
                           or session.get("tenant_id")
                           or DEFAULT_TENANT_ID)
                decision = evaluate_cached(tid)
            except Exception:  # noqa: BLE001 — لا نكسر اللوحة على باج فحص
                decision = None
            if decision is not None and decision.blocks_panel:
                from flask import redirect, url_for
                if decision.state == LifecycleState.NEVER_ACTIVATED:
                    target = "radius.license_activate_page"
                else:
                    # EXPIRED أو SYNC_OUTAGE_BEYOND_GRACE → نفس الصفحة
                    # (مع تفسير مختلف داخل القالب حسب decision.state).
                    target = "radius.license_expired_page"
                if request.method in ("GET", "HEAD"):
                    return redirect(url_for(target))
                # كتابة على نسخة مقفولة → 403 صريح.
                abort(403)

        if name not in _PROVIDER_GATE_SKIP:
            blocked = False
            skey = ""
            try:
                from ..auth.provider_gate import is_endpoint_blocked_by_provider
                from ..core.tenant import DEFAULT_TENANT_ID
                tid = int(getattr(g, "tenant_id", None)
                           or session.get("tenant_id")
                           or DEFAULT_TENANT_ID)
                blocked, skey = is_endpoint_blocked_by_provider(tid, name)
            except Exception:  # noqa: BLE001 — fail-open (لا نكسر اللوحة)
                # تعطّل تزامن المزوّد لا يَكسر اللوحة — السماح هو الافتراضي.
                pass
            if blocked:
                # خدمة موقوفة من المزوّد — لا تخطّي من السوبر.
                # GET → صفحة blocked الودودة. الكتابة → 403 (يُترجَم في الـUI).
                from flask import redirect, url_for
                if request.method in ("GET", "HEAD"):
                    return redirect(url_for(
                        "radius.provider_blocked_page", service=skey))
                abort(403)

            # ── (0c) فحص locked_upgrade — «مدفوعة-غير-مفعّلة». ──
            # الخدمة مرئية في السايدبار بشارة قفل، الرابط يُعيد إلى صفحة
            # «طلب تفعيل / ترقية» (ليست hard-block ولا hidden). للسوبر-أدمن
            # أيضًا — قرار تجاري فوق RBAC. الكتابة على خدمة locked_upgrade
            # نَردّها بـ403 لئلّا تَنفلت كتابة عابرة.
            try:
                from ..auth.provider_gate import is_endpoint_requires_upgrade
                needs_up, up_skey = is_endpoint_requires_upgrade(tid, name)
            except Exception:  # noqa: BLE001
                needs_up, up_skey = False, ""
            if needs_up:
                from flask import redirect, url_for
                if request.method in ("GET", "HEAD"):
                    return redirect(url_for(
                        "radius.provider_upgrade_page", service=up_skey))
                abort(403)

            # ── (0d) حارس القدرات «افتراضيًّا مُطفأة» (default-off). ──
            # ميزات مخفيّة حتى يَمنحها المزوّد صراحةً (مثل «إدارة أقسام
            # الواجهة» → مفتاح القدرة «sections»). عكس الافتراضي fail-open:
            # الغياب/عدم-المنح = محجوب. سوبر-أدمن لا يَتجاوز (قرار تجاري فوق
            # RBAC). الـGET يُعاد للوحة التحكّم بهدوء (البند مخفيّ أصلًا)،
            # والكتابة 403. fail-closed عند خطأ التحقّق لنقطة قدرة معروفة.
            from ..auth.provider_gate import (
                capability_key_for_endpoint, is_endpoint_capability_granted)
            cap_key = capability_key_for_endpoint(name)
            if cap_key:
                try:
                    cap_granted, _ = is_endpoint_capability_granted(tid, name)
                except Exception:  # noqa: BLE001 — fail-closed لقدرة default-off
                    cap_granted = False
                if not cap_granted:
                    from flask import redirect, url_for
                    if request.method in ("GET", "HEAD"):
                        return redirect(url_for("radius.dashboard"))
                    abort(403)

        # ── (3) حارس أعلام القسم — يُقدّم على RBAC لأنّه حظر مستوى
        #        المستأجر بالكامل، ولا مَعنى لقياس صلاحيات داخل قسم
        #        مُغلَق أساسًا. السوبر دائمًا يَتجاوز. ──
        if not is_super and is_section_blocked(name):
            abort(403)

        # ── (1) حارس الكتابة/السوبر ──
        required = _PERM_GUARDED.get(name)
        if required is not None and not (
            name in _PERM_WRITE_ONLY
            and request.method in ("GET", "HEAD", "OPTIONS")
        ):
            if not is_super:
                if required == _PERM_SUPER or required not in perms:
                    abort(403)

        # ── (2) حارس العرض على القراءة (مطابقة الشريط الجانبي) ──
        if (
            request.method in ("GET", "HEAD", "OPTIONS")
            and not is_super
            and name not in _NAV_VIEW_GUARD_SKIP
        ):
            view_required = _NAV_PERM.get(name)
            if view_required is not None:
                if view_required == _PERM_SUPER or view_required not in perms:
                    abort(403)
        return None


def _wants_json_response() -> bool:
    """True when the caller expects JSON (AJAX/fetch) rather than an HTML page.

    Mirrors the in-panel modals which POST via fetch with X-Requested-With or an
    application/json Accept header. Falls back to request.is_json for raw JSON
    bodies. A browser navigation (Accept: text/html) always returns False.
    """
    xrw = (request.headers.get("X-Requested-With") or "").lower()
    if xrw in ("fetch", "xmlhttprequest"):
        return True
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return True
    try:
        return bool(request.is_json)
    except Exception:  # noqa: BLE001
        return False


def _install_error_handlers(bp: Blueprint) -> None:
    """Friendly 403 for RBAC / gate denials.

    The permission guard (_perm_guard) and the lifecycle/provider gates
    abort(403) on an unauthorized write (e.g. a limited admin saving an edit they
    can't see). Without a handler Flask returns the bare werkzeug «403 Forbidden»
    page, which drops the operator out of the panel chrome and reads like a
    crash. Here we render the in-panel «forbidden» page (radius/forbidden_403.html)
    for browser navigations, or a clean JSON body the frontend toast can show for
    AJAX/JSON calls. Enforcement is unchanged — the status stays 403; only the
    body is made friendly. The wording is owner-approved (see the template).
    """
    from flask import jsonify, render_template

    # Owner-approved wording — keep in sync with radius/forbidden_403.html.
    _MSG = "ليس لديك صلاحية الوصول إلى هذه الصفحة"
    _SUB = "إذا كنت تتوقع أن هذا خلل، راجع الإدارة."

    @bp.errorhandler(403)
    def _friendly_forbidden(err):  # noqa: ANN001
        if _wants_json_response():
            return jsonify({"ok": False, "error": _MSG}), 403
        try:
            return render_template("radius/forbidden_403.html"), 403
        except Exception:  # noqa: BLE001 — never 500 the operator over chrome
            return (
                '<h1 dir="rtl" lang="ar" style="font-family:sans-serif">'
                "403 — ممنوع</h1><p dir=\"rtl\">" + _MSG + "</p>"
                '<p dir="rtl">' + _SUB + "</p>",
                403,
                {"Content-Type": "text/html; charset=utf-8"},
            )


__all__ = ["get_radius_blueprint"]
