"""factory رئيسية لـ radius blueprint — يضم كل الأقسام + الـ auth + health/readiness."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

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
    from .network_device_bypass import register_network_device_bypass_routes
    from .network_ip_scan import register_network_ip_scan_routes
    from .remote_device_access import register_remote_device_access_routes
    from .router_events import register_router_events_routes
    from .network_telegram_settings import register_network_telegram_routes
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

    register_dashboard_routes(bp)
    register_account_routes(bp)
    register_devices_routes(bp)
    register_network_devices_routes(bp)
    register_network_device_bypass_routes(bp)
    register_network_ip_scan_routes(bp)
    register_remote_device_access_routes(bp)
    register_router_events_routes(bp)
    register_network_telegram_routes(bp)
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

    from .tools import register_tools_routes
    register_tools_routes(bp)

    from .settings import register_settings_routes
    register_settings_routes(bp)

    from .overviews import register_overview_routes
    register_overview_routes(bp)

    from .subscribers_overview import register_subscribers_overview_routes
    register_subscribers_overview_routes(bp)

    from .share_groups import register_share_groups_routes
    register_share_groups_routes(bp)

    # تصدير الجداول الموحّد (PDF/XLSX/CSV لأي جدول في الواجهة)
    from .table_export import register_table_export_routes
    register_table_export_routes(bp)

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
        return None


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
    # حفظ إعدادات النظام — تتطلّب صلاحية settings.edit (تُفحص على الكتابة فقط)
    "settings_page": "settings.edit",
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
    "rep_login_status": "reports.view", "rep_login_states": "reports.view",
    "rep_login_states_cards": "reports.view", "rep_login_states_subscribers": "reports.view",
    "rep_login_states_sub_portal": "reports.view", "rep_login_states_card_store": "reports.view",
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


def _install_permission_guard(bp: Blueprint) -> None:
    """RBAC server-side: يمنع الأدوار المحدودة من المسارات الحسّاسة."""
    from flask import session, abort

    @bp.before_request
    def _perm_guard():
        ep = request.endpoint or ""
        if not ep.startswith("radius."):
            return None
        name = ep.split(".", 1)[1]
        required = _PERM_GUARDED.get(name)
        if required is None:
            return None
        # المسارات التي تخدم GET للعرض و POST للحفظ: نحرس الكتابة فقط
        if name in _PERM_WRITE_ONLY and request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        # super_admin يمرّ دائمًا
        if session.get("is_super_admin"):
            return None
        if required == _PERM_SUPER:
            abort(403)
        perms = session.get("permissions") or []
        if required not in perms:
            abort(403)
        return None


__all__ = ["get_radius_blueprint"]
