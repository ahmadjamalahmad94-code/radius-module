"""Central permission guard for every authenticated ``/api/v1`` endpoint.

p01/D06 (2026-09-29): the API authenticated the token and then trusted it —
a manager holding only «لوحة التحكّم» credited wallets, voided ledger
entries, changed system settings, generated cards and read voucher codes,
the ledger, payments, the audit log, tenants and the license file. The web
panel refused all of that.

This module is ONE choke point, run by ``auth.enforce_api_auth`` right after a
credential is accepted (so it covers the ``require_api_token`` decorator and
the optional global /api guard alike):

  * Unbound credentials (env tokens / tokens with no ``created_by`` — the
    HobeHub / licensing-bridge integrations) keep their current behaviour.
  * The owner / co-owner behind the token bypasses the guard
    (``radius.auth.owner.is_owner_like`` — the SAME predicate as the web).
  * Every other admin is checked against ``API_PERMISSIONS``: each endpoint
    maps to the same decision as its web equivalent. An endpoint that is not
    mapped is DENIED (deny by default) unless it is in ``API_AUTH_ONLY``.

Spec grammar (a value of ``API_PERMISSIONS``; a dict maps HTTP method → spec):

  ``"web:<endpoint>"``  → ``rbac_denial_status`` of the web endpoint
                          ``radius.<endpoint>`` for the request method — role
                          keys, section flags, per-manager sections, action
                          gates, bulk gate and the daily rate limit, exactly
                          like the web panel.
  ``"__super__"``       → owner / co-owner only.
  ``"a.b|c.d"``         → the admin holds ANY of the listed role keys.
  ``"mt:<perm>"``       → MikroTik-domain permission (``mt_permissions.has``),
                          the ``requires_perm`` decorator of the web page.
  ``"grant:<flag>"``    → per-manager grant of ``ManagerDistributorOpsService``
                          (e.g. ``can_import_batches``), like the web handler.

``tests/test_api_permission_guard.py`` fails when an authenticated API
endpoint is neither mapped nor allow-listed.
"""
from __future__ import annotations

from typing import Any, Optional, Union

from flask import request

from .responses import fail

Spec = Union[str, dict]

SUPER = "__super__"

_DENIED_AR = "ليس لديك صلاحية لتنفيذ هذا الإجراء."
_UNMAPPED_AR = ("هذه النقطة غير مربوطة بصلاحية بعد — متاحة للمالك فقط. "
                "راجع المالك.")

# ─────────────────────────────────────────────────────────────────────────
# Endpoints that need an authenticated admin but no permission key: they only
# touch the caller's OWN account / device / notifications, or return static
# metadata the app needs to render itself. Keep this list SHORT.
# ─────────────────────────────────────────────────────────────────────────
API_AUTH_ONLY: dict[str, str] = {
    "admin_me": "own profile",
    "admin_password": "own password (throttled, current password required)",
    "admin_logout": "own session",
    "v1.notifications_list": "own admin notifications (web: allow-listed)",
    "v1.notifications_unread_count": "own admin notifications",
    "v1.notifications_get": "own admin notifications",
    "v1.notifications_mark_read": "own admin notifications",
    "v1.notifications_read_all": "own admin notifications",
    "v1.devices_push_token_register": "own device FCM token",
    "v1.devices_push_token_unregister": "own device FCM token",
    "v1.dashboard_get": "home screen (web dashboard is open to every admin)",
    "v1.provider_grants": "provider contract the app needs to render menus",
    "v1.api_contracts": "static API contract metadata",
    "v1.permissions_catalog": "static permission catalogue (names only)",
    "v1.tools_catalog": "own per-tool permission map (the tools screen)",
}


# Endpoints that are NOT behind the admin API credential at all (own auth:
# hotspot-card / subscriber-portal / store-customer tokens, the FreeRADIUS
# internal secret, or public by nature). Listed so a NEW unauthenticated
# endpoint is noticed by tests/test_api_permission_guard.py.
API_PUBLIC: frozenset[str] = frozenset({
    "_api_root", "admin_login", "openapi_json", "openapi_docs",
    "v1.health", "v1.version",
    "v1.hotspot_cards_login", "v1.hotspot_cards_me", "v1.hotspot_cards_catalog",
    "v1.hotspot_cards_my_cards", "v1.hotspot_cards_purchase",
    "v1.hotspot_cards_send_sms",
    "v1.subscriber_portal_login", "v1.subscriber_portal_logout",
    "v1.subscriber_portal_me", "v1.subscriber_portal_dashboard",
    "v1.subscriber_portal_requests", "v1.subscriber_portal_request_detail",
    "v1.subscriber_portal_loan_request", "v1.subscriber_portal_renewal_request",
    "v1.store_ping", "v1.store_register", "v1.store_login", "v1.store_logout",
    "v1.store_me", "v1.store_packages", "v1.store_my_cards", "v1.store_purchases",
    "v1.store_redeem", "v1.store_purchase", "v1.store_payment_methods",
    "v1.store_deposits_list", "v1.store_deposit_create",
    "v1.store_withdrawals_list", "v1.store_withdrawal_create",
    "v1.store_chat_poll", "v1.store_chat_post", "v1.store_chat_unread",
    "v1.internal_auth", "v1.internal_postauth", "v1.internal_diag",
})


def _npc(prefix: str, key: str, children: tuple[str, ...]) -> dict[str, Spec]:
    """Network Policy Center — same mikrotik-domain keys as the web pages."""
    view, manage = f"mt:npc.{key}.view", f"mt:npc.{key}.manage"
    preview, apply_ = f"mt:npc.{key}.preview", f"mt:npc.{key}.apply"
    out: dict[str, Spec] = {
        f"v1.{prefix}_list": view, f"v1.{prefix}_get": view,
        f"v1.{prefix}_changes": view,
        f"v1.{prefix}_create": manage, f"v1.{prefix}_patch": manage,
        f"v1.{prefix}_delete": manage, f"v1.{prefix}_duplicate": manage,
        f"v1.{prefix}_preview": preview, f"v1.{prefix}_script": preview,
        f"v1.{prefix}_apply": apply_, f"v1.{prefix}_rollback": apply_,
    }
    for child in children:
        out[f"v1.{prefix}_{child}_list"] = view
        out[f"v1.{prefix}_{child}_add"] = manage
        out[f"v1.{prefix}_{child}_delete"] = manage
    return out


def _same(spec: Spec, *names: str) -> dict[str, Spec]:
    return {f"v1.{n}": spec for n in names}


API_PERMISSIONS: dict[str, Spec] = {
    # ── introspection ──
    "v1._routes_list": "api.use",

    # ── admin alerts (Telegram) — web admin_alerts_* ──
    "v1.alerts_telegram_get": "web:admin_alerts_page",
    "v1.alerts_telegram_bot_save": "web:admin_alerts_save_bot",
    "v1.alerts_telegram_test_connection": "web:admin_alerts_test_connection",
    "v1.alerts_telegram_toggle": "web:admin_alerts_toggle",
    "v1.alerts_telegram_alert_test": "web:admin_alerts_test",

    # ── store support (money: deposit/withdrawal confirmation) ──
    "v1.store_admin_support": "web:store_support",
    "v1.store_admin_deposit_confirm": "web:store_support_deposit_confirm",
    "v1.store_admin_deposit_reject": "web:store_support_deposit_reject",
    "v1.store_admin_withdrawal_confirm": "web:store_support_withdrawal_confirm",
    "v1.store_admin_withdrawal_reject": "web:store_support_withdrawal_reject",
    "v1.store_admin_pm_list": "web:store_support",
    "v1.store_admin_pm_create": "web:store_support_payment_method_create",
    "v1.store_admin_pm_update": "web:store_support_payment_method_update",
    "v1.store_admin_pm_delete": "web:store_support_payment_method_update",
    "v1.store_admin_chat_get": "web:store_support",
    "v1.store_admin_chat_post": "web:store_support_chat_post",
    "v1.store_admin_chat_status": "web:store_support_chat_status",

    # ── MikroTik programming (web requires_perm mikrotik.program/rollback) ──
    "v1.mt_program_get": "mt:mikrotik.program",
    "v1.mt_program_plan": "mt:mikrotik.program",
    "v1.mt_program_apply": "mt:mikrotik.program",
    "v1.mt_program_unprogram": "mt:mikrotik.rollback",

    # ── subscribers (legacy accounts + actions) — same web endpoints the
    #    in-handler guards already use ──
    "v1.accounts_list": "web:subscribers_list",
    "v1.accounts_get": "web:subscribers_list",
    "v1.accounts_usage": "web:subscribers_list",
    "v1.accounts_360": "web:subscribers_list",
    "v1.accounts_actions_context": "web:subscribers_list",
    "v1.accounts_create": "web:users_create",
    "v1.accounts_patch": "web:users_update",
    "v1.accounts_reset_pw": "web:users_update",
    "v1.accounts_delete": "web:users_delete",
    "v1.accounts_extend": "web:users_extend",
    "v1.accounts_disable": "web:users_toggle",
    "v1.accounts_enable": "web:users_toggle",
    "v1.accounts_action_extend": "web:users_extend",
    "v1.accounts_action_change_plan": "web:users_change_plan",
    "v1.accounts_action_quota_topup": "web:users_quota_topup",
    "v1.accounts_action_quota_reset": "web:users_quota_reset_daily",
    "v1.accounts_action_payment": "web:users_payment_create",
    "v1.accounts_action_balance": "web:users_balance_add",
    "v1.accounts_action_loan": "web:users_loan_create",
    "v1.accounts_action_message": "web:users_send_sms",
    "v1.accounts_action_send_credentials": "web:users_send_credentials",
    "v1.accounts_action_rename": "web:users_update",
    "v1.accounts_action_disconnect": "web:online_disconnect",

    # ── cards ──
    "v1.cards_generate": "web:cards_generate",
    "v1.cards_batches_import": "grant:can_import_batches",
    "v1.cards_batches_list": "web:cards_batches",
    "v1.cards_batches_bulk": "web:cards_batches_bulk",
    "v1.cards_batches_export_csv": "web:cards_batches_export_csv",
    "v1.cards_batches_export_xlsx": "web:cards_batches_export_xlsx",
    "v1.cards_batches_export_pdf": "web:cards_batches_export_pdf",
    "v1.cards_recharge_list": "web:cards_recharge_list",
    "v1.cards_recharge_generate": "web:cards_recharge_new",
    "v1.cards_recharge_get": "web:cards_recharge_batch",
    "v1.cards_recharge_cards": "web:cards_recharge_batch",
    "v1.cards_recharge_delete": "web:cards_recharge_batch_delete",
    "v1.cards_batch_get": "web:cards_batches",
    "v1.cards_batch_summary": "web:cards_batches",
    "v1.cards_batch_update": "web:cards_batch_edit",
    "v1.cards_of_batch": "web:cards_of_batch",
    "v1.cards_get": "web:cards_list",
    "v1.cards_revoke": "web:cards_revoke",
    "v1.cards_enable": "web:cards_checker",
    "v1.cards_disable": "web:cards_checker",
    "v1.cards_lock_mac": "web:cards_checker",
    "v1.cards_unlock_mac": "web:cards_checker",
    "v1.cards_reset_usage": "web:cards_checker",
    "v1.cards_disconnect": "web:cards_checker",
    # «إضافة/خصم وقت» — the checker's set_time action on the web (cards.verify)
    "v1.cards_adjust_time": "web:cards_checker",
    # irreversible (p01/D25) — owner / co-owner only
    "v1.cards_delete_permanent": SUPER,
    "v1.cards_check": "web:cards_checker_api_lookup",

    # ── card store users + marketplace ──
    "v1.card_users_list": "web:card_users_list",
    "v1.card_users_create": "web:card_users_create",
    "v1.card_user_360": "web:card_user_360",
    "v1.card_user_recharge": "web:card_user_recharge",
    "v1.card_user_purchase": "web:card_user_purchase",
    "v1.card_user_password": "web:card_user_password",
    "v1.card_marketplace_packages": "web:card_marketplace",
    "v1.card_marketplace_package_create": "web:card_marketplace_package_create",

    # ── communications / WhatsApp ──
    "v1.communications_summary": "web:communications",
    "v1.communications_templates_list": "web:communications_templates",
    "v1.communications_templates_create": "web:communications_templates",
    "v1.communications_audience_list": "web:communications_audience",
    "v1.communications_audience_create": "web:communications_audience",
    "v1.communications_audience_preview": "web:communications_audience",
    "v1.communications_send": "web:communications_send",
    "v1.communications_campaigns_list": "web:communications_campaigns",
    "v1.communications_campaigns_dry_run": "web:communications_campaigns",
    "v1.communications_deliveries": "web:communications_deliveries",
    "v1.communications_channels": "web:communications_channels",
    "v1.communications_channel_save": "web:communications_channels",
    "v1.whatsapp_state": "web:whatsapp",
    "v1.whatsapp_settings_save": "web:whatsapp_settings",
    "v1.whatsapp_test_send": "web:whatsapp_test",
    "v1.whatsapp_cloud_test_send": "web:whatsapp_cloud_test",
    "v1.whatsapp_bot_get": "web:communications_bot",
    "v1.whatsapp_bot_save": "web:communications_bot",
    "v1.customer_portals_overview": "web:customer_portals_admin",

    # ── loans / payments / ledger (money) ──
    "v1.loans_list": "users.view",
    "v1.loans_get": "users.view",
    "v1.loans_create": "web:users_loan_create",
    "v1.loans_settle": "web:users_loan_settle",
    "v1.payments_list": "users.payments|reports.finance",
    "v1.payments_create": "web:users_payment_create",
    # the web voids money only through the ledger void (owner only)
    "v1.payments_void": "web:finance_ledger_void",
    "v1.ledger_list": "web:finance_ledger",
    "v1.ledger_void": "web:finance_ledger_void",

    # ── recycle bin ──
    "v1.recycle_bin_list": "web:recycle_bin",
    # archiving admins/roles/NAS/plans/subscribers from one generic endpoint
    # is an owner-level operation (the web archives each from its own page).
    "v1.recycle_bin_archive": SUPER,
    "v1.recycle_bin_restore": "web:recycle_bin_restore",

    # ── lifecycle / retention ──
    "v1.lifecycle_policies": {"GET": "web:lifecycle_settings",
                              "POST": "web:lifecycle_policy_create"},
    "v1.lifecycle_policy": "web:lifecycle_policy_create",
    "v1.lifecycle_policy_disable": "web:lifecycle_policy_disable",
    "v1.lifecycle_preview": "settings.view",
    "v1.lifecycle_run": "web:lifecycle_run",

    # ── payment collection (provider-only, like the web collection hub) ──
    **_same(SUPER,
            "payment_collection_settings_get", "payment_collection_settings_patch",
            "payment_collection_requests_list", "payment_collection_requests_create",
            "payment_collection_requests_get",
            "payment_collection_request_instructions",
            "payment_collection_submit_proof", "payment_collection_review_queue",
            "payment_collection_reconciliation", "payment_collection_approve",
            "payment_collection_reject", "payment_collection_apply_service",
            # provider webhook — sent with an unbound integration token
            "payment_collection_jawwal_webhook"),

    # ── distributors ──
    "v1.distributors_list": "web:distributors_list",
    "v1.distributors_summary": "web:distributors_detail",
    "v1.distributors_batches": "web:distributors_detail",
    "v1.distributors_create": "web:distributors_create",
    "v1.distributors_assign_batch": "web:distributors_assign_batch",
    "v1.distributors_settle": "web:distributors_settle",

    # ── reports ──
    "v1.reports_snapshots_list": "web:reports_archive",
    "v1.reports_snapshots_get": "web:reports_archive",
    "v1.reports_snapshots_create": "web:reports_archive_create",
    **_same("web:finance_reports", *[
        f"reports_{kind}{suffix}"
        for kind in ("sales", "sales_daily", "sales_monthly", "sales_yearly",
                     "payments", "loans", "card_sales", "profit_loss",
                     "distributor_debts")
        for suffix in ("", "_export_csv", "_export_xlsx", "_export_pdf")]),
    **_same("web:reports_home", *[
        f"reports_activations{suffix}"
        for suffix in ("", "_export_csv", "_export_xlsx", "_export_pdf")]),
    "v1.reports_login_states_overview": "web:rep_login_states",
    "v1.reports_login_states_detail": "web:rep_login_states",
    "v1.operational_reports_detail": "web:reports_home",

    # ── speed profiles / schedules / share groups ──
    "v1.bandwidth_profiles_list": "web:bw_list",
    "v1.bandwidth_profiles_get": "web:bw_list",
    "v1.bandwidth_profiles_create": "web:bw_create",
    "v1.bandwidth_profiles_patch": "web:bw_update",
    "v1.bandwidth_profiles_delete": "web:bw_delete",
    "v1.bandwidth_schedules_list": "web:bandwidth_schedules",
    "v1.bandwidth_schedules_effective": "web:bandwidth_schedules",
    "v1.bandwidth_schedules_create": "web:bandwidth_schedules_create",
    "v1.bandwidth_schedules_apply": "web:bandwidth_schedules_apply",
    "v1.share_groups_list": "web:sgrp_list",
    "v1.share_groups_get": "web:sgrp_list",
    "v1.share_groups_create": "web:sgrp_create",
    "v1.share_groups_patch": "web:sgrp_update",
    "v1.share_groups_delete": "web:sgrp_delete",
    "v1.share_groups_add_member": "web:sgrp_add_member",
    "v1.share_groups_remove_member": "web:sgrp_remove_member",

    # ── print templates / print jobs (cards.print) ──
    **_same("web:print_templates",
            "print_templates_list", "print_templates_presets",
            "print_templates_last_settings_get", "print_jobs_list",
            "print_templates_get", "print_templates_background_image",
            "print_templates_thumbnail_svg", "print_templates_preview_fragment",
            "print_jobs_get", "print_jobs_download"),
    # non-numeric job id → JSON 404 on every verb (fix2 10); same key as the
    # print pages for every method
    **_same({"GET": "web:print_templates", "*": "web:print_templates_export_job_start"},
            "print_jobs_bad_id", "print_jobs_bad_id_download",
            "print_jobs_bad_id_cancel"),
    "v1.print_templates_create": "web:print_templates_create",
    "v1.print_templates_quick_save": "web:print_templates_create",
    "v1.print_templates_update": "web:print_templates_update",
    "v1.print_templates_quick_elements": "web:print_templates_update",
    "v1.print_templates_background": "web:print_templates_update",
    "v1.print_templates_last_settings_put": "web:print_templates_update",
    "v1.print_templates_delete": "web:print_templates_delete",
    "v1.print_templates_set_default": "web:print_templates_set_default",
    "v1.print_templates_cleanup_fixtures": "web:print_templates_cleanup_fixtures",
    "v1.print_templates_preview_pdf": "web:print_templates_preview",
    "v1.print_templates_render": "web:print_templates_preview",
    "v1.print_templates_export": "web:print_templates_export_job_start",
    "v1.print_templates_export_pdf": "web:print_templates_export_job_start",
    "v1.print_templates_export_job_start": "web:print_templates_export_job_start",
    "v1.print_jobs_cancel": "web:print_templates_export_job_start",
    "v1.print_jobs_cancel_delete": "web:print_templates_export_job_start",

    # ── backups (owner only, like every web backups_* route) ──
    "v1.backups_status": "web:backups",
    "v1.backups_run": "web:backups_run",
    "v1.backups_google_drive_connect": "web:backups_gdrive_start",
    "v1.backups_google_drive_poll": "web:backups_gdrive_poll",
    "v1.backups_google_drive_status": "web:backups",

    # ── plans (RADIUS profiles) ──
    "v1.profiles_list": "web:plans_list",
    "v1.profiles_get": "web:plans_list",
    "v1.profiles_create": "web:plans_create",
    "v1.profiles_patch": "web:plans_update",
    "v1.profiles_delete": "web:plans_delete",

    # ── NAS / network devices / pools ──
    "v1.nas_list": "web:devices_list",
    "v1.nas_get": "web:devices_list",
    "v1.nas_create": "web:devices_create",
    "v1.nas_patch": "web:devices_update",
    "v1.nas_delete": "web:devices_delete",
    "v1.nas_test": "web:devices_test",
    "v1.network_devices_list": "web:network_devices_list",
    "v1.network_devices_get": "web:network_devices_list",
    "v1.network_devices_create": "web:network_devices_create",
    "v1.network_devices_patch": "web:network_devices_update",
    "v1.network_devices_delete": "web:network_devices_delete",
    "v1.network_devices_check": "web:network_devices_check",
    "v1.network_devices_scan_router": "web:network_ip_scan_page",
    "v1.network_devices_scan_add": "web:network_ip_scan_add",
    "v1.network_devices_bypass_state": "web:network_devices_list",
    "v1.network_devices_bypass_apply": "web:network_device_bypass_apply",
    "v1.network_devices_bypass_remove": "web:network_device_bypass_remove",
    "v1.network_devices_remote_access_state": "web:remote_device_access_form",
    "v1.network_devices_remote_access_open": "web:remote_device_access_open",
    "v1.network_devices_remote_access_close": "web:remote_device_access_close",
    "v1.pools_list": "web:pool_list",
    "v1.pools_get": "web:pool_list",
    "v1.pools_create": "web:pool_create",
    "v1.pools_patch": "web:pool_update",
    "v1.pools_delete": "web:pool_delete",
    "v1.devices_by_mac": "nas.view",
    "v1.devices_list": "nas.view",
    "v1.devices_sync": "nas.edit",
    # router push feeds (normally an owner / unbound integration token)
    "v1.devices_ingest": "nas.edit",
    "v1.router_metrics_ingest": "nas.edit",
    "v1.accounting_event_ingest": "nas.edit",
    "v1.router_alerts_settings_get": "mt:mikrotik.diagnostics",
    "v1.router_alerts_settings_patch": "mt:mikrotik.diagnostics",

    # ── online sessions / accounting ──
    "v1.sessions_online": "web:online_list",
    "v1.sessions_disconnect": "web:online_disconnect",
    "v1.sessions_lock_mac": "web:online_lock_mac",
    "v1.sessions_lock_ip": "web:online_lock_ip",
    "v1.sessions_temp_speed": "web:online_temp_speed",
    "v1.sessions_temp_speed_cancel": "web:online_temp_speed_cancel",
    "v1.accounting_online": "web:online_list",
    "v1.accounting_list": "users.view|online.view|reports.view",
    "v1.accounting_sessions_history": "users.view|online.view|reports.view",
    "v1.accounting_session_detail": "users.view|online.view|reports.view",
    "v1.accounting_usage_subscriber": "users.view",
    "v1.accounting_usage_tenant": "reports.view",
    "v1.accounting_usage_plan": "plans.view|reports.view",
    "v1.accounting_quota_check": "users.view",

    # ── webhooks ──
    "v1.webhooks_get": "web:wh_settings",
    "v1.webhooks_set": "web:wh_settings",
    "v1.webhooks_test": "settings.edit",
    "v1.webhooks_deliveries": "web:wh_deliveries",

    # ── setup wizard (v3 API) — same split as the web v3 pages ──
    **_same("web:setup_wizard_v3_page",
            "setup_wizard_overview", "setup_wizard_health",
            "setup_wizard_server_readiness", "setup_wizard_runs_state",
            "setup_wizard_phase_planners", "setup_wizard_diagnostics_catalogue",
            "setup_wizard_router_services_catalogue"),
    "v1.setup_wizard_runs_create": "web:setup_wizard_v3_create_run",
    "v1.setup_wizard_router_info": "web:setup_wizard_v3_router_info",
    "v1.setup_wizard_generate_script": "web:setup_wizard_v3_generate_script",
    "v1.setup_wizard_submit_key": "web:setup_wizard_v3_submit_key",
    "v1.setup_wizard_apply_server_peer": "web:setup_wizard_v3_apply_peer",
    "v1.setup_wizard_mark_handshake": "web:setup_wizard_v3_mark_handshake",
    "v1.setup_wizard_register_router": "web:setup_wizard_v3_register",
    "v1.setup_wizard_phase_plan": "web:setup_wizard_v3_phase_plan",
    "v1.setup_wizard_router_services_status":
        "web:setup_wizard_v3_router_services_status",

    # ── MikroTik integration CRUD ──
    "v1.mt_list": "web:mt_list",
    "v1.mt_add": "web:mt_create",
    "v1.mt_update": "web:mt_update",
    "v1.mt_delete": "web:mt_delete",
    "v1.mt_test": "web:mt_test",
    "v1.mt_test_creds": "nas.create",

    # ── MikroTik live control: reads = nas.view, writes = nas.edit ──
    **_same("nas.view",
            "mt_system_resource", "mt_system_health", "mt_system_identity",
            "mt_system_clock", "mt_system_routerboard", "mt_system_overview",
            "mt_interfaces", "mt_interface_traffic", "mt_interface_sse",
            "mt_interfaces_stream", "mt_ip_addresses", "mt_ip_routes",
            "mt_ip_neighbors", "mt_router_health", "mt_hotspot_active",
            "mt_ppp_active", "mt_queues_simple_list", "mt_firewall_filter",
            "mt_firewall_nat", "mt_log_tail", "mt_counters",
            "mt_guided_assistant"),
    "v1.mt_hotspot_disconnect": "web:online_disconnect",
    "v1.mt_ppp_disconnect": "web:online_disconnect",
    "v1.mt_address_lists": {"GET": "nas.view", "POST": "nas.edit"},
    **_same("nas.edit",
            "mt_queues_simple_set", "mt_address_list_remove", "mt_tool_ping",
            "mt_tool_traceroute", "mt_tool_dns_resolve", "mt_files_list",
            "mt_system_backup_save", "mt_file_download", "mt_system_reboot",
            "mt_system_identity_set", "mt_system_ntp_sync",
            "mt_ip_dns_cache_flush", "mt_backups_list", "mt_backup_manifest",
            "mt_backup_delete"),
    "v1.mt_backup_restore": "mt:mikrotik.restore",
    "v1.mt_topology": "mt:mikrotik.view",
    "v1.mt_login_designer_state": "mt:mikrotik.view",
    "v1.mt_login_designer_save": "mt:mikrotik.manage",
    "v1.mt_login_designer_preset_save": "mt:mikrotik.manage",
    "v1.mt_login_designer_preset_apply": "mt:mikrotik.manage",
    "v1.mt_login_designer_preset_delete": "mt:mikrotik.manage",
    "v1.mt_audit_timeline": "mt:mikrotik.view",
    "v1.mt_problems": "mt:mikrotik.diagnostics",
    "v1.mt_recovery_plan": "mt:mikrotik.view",
    "v1.mt_permission_matrix": "mt:mikrotik.audit.view",
    "v1.mt_push_setup": "web:mt_push_setup",
    "v1.mt_metrics_setup": "mt:mikrotik.diagnostics",
    "v1.site_exit_state": "mt:site_exit.view",
    "v1.site_exit_policy_create": "mt:site_exit.manage",
    "v1.site_exit_plan": "mt:site_exit.preview",
    **_npc("npc_ra", "remote_access", ()),
    **_npc("npc_wb", "web_block", ("target",)),
    **_npc("npc_wg", "walled_garden", ("entry",)),

    # ── admins / roles / audit / tenants ──
    "v1.admins_list": "web:admins_list",
    "v1.admins_get": "web:admins_list",
    "v1.admins_create": "web:admins_create",
    "v1.admins_patch": "web:admins_update",
    "v1.admins_delete": "web:admins_delete",
    "v1.roles_list": "web:admins_list",
    "v1.roles_get": "web:admins_list",
    "v1.roles_create": "web:roles_create",
    "v1.roles_patch": "web:roles_update",
    "v1.roles_delete": "web:roles_delete",
    "v1.audit_list": "audit.view",
    "v1.tenants_list": "web:tenants_list",
    "v1.tenants_get": "web:tenants_list",
    "v1.tenants_create": "web:tenants_create",
    "v1.tenants_patch": "web:tenants_update",

    # ── vouchers / invoices / tickets / services / service requests ──
    "v1.vouchers_list": "web:vch_list",
    "v1.vouchers_generate": "web:vch_generate",
    "v1.vouchers_revoke": "web:vch_revoke",
    "v1.invoices_list": "web:inv_list",
    "v1.invoices_get": "web:inv_list",
    "v1.invoices_create": "web:inv_create",
    "v1.invoices_status": "web:inv_status",
    "v1.tickets_list": "web:tk_list",
    "v1.tickets_get": "web:tk_list",
    "v1.tickets_create": "web:tk_create",
    "v1.tickets_patch": "web:tk_status",
    "v1.tickets_reply": "web:tk_reply",
    "v1.services_list": "web:svc_list",
    "v1.services_get": "web:svc_list",
    "v1.services_create": "web:svc_create",
    "v1.services_patch": "web:svc_update",
    "v1.services_delete": "web:svc_delete",
    "v1.service_requests_list": "web:service_request_list",
    "v1.service_requests_create": "web:service_request_create",
    "v1.service_requests_decision": SUPER,

    # ── subscriber groups ──
    "v1.subscriber_groups_list": "web:subscriber_groups_list",
    "v1.subscriber_groups_get": "web:subscriber_groups_list",
    "v1.subscriber_groups_create": "web:subscriber_groups_create",
    "v1.subscriber_groups_patch": "web:subscriber_groups_update",
    "v1.subscriber_groups_delete": "web:subscriber_groups_delete",
    "v1.subscriber_groups_disconnect_online":
        "web:subscriber_groups_disconnect_online",
    "v1.subscriber_groups_quota_reset_daily":
        "web:subscriber_groups_quota_reset_daily",

    # ── events center ──
    "v1.events_list": "web:events_center",
    "v1.events_detail": "web:events_detail",
    "v1.events_risk": "web:events_risk",
    "v1.events_risk_run": "web:events_risk",
    "v1.events_security": "web:events_security",
    "v1.events_investigations_list": "web:events_investigations",
    "v1.events_investigations_create": "web:events_investigations",

    # ── network Telegram ──
    "v1.network_telegram_get": "web:network_telegram_settings",
    "v1.network_telegram_save": "settings.edit",
    "v1.network_telegram_test": "settings.edit",

    # ── device health ──
    "v1.device_health_overview": "web:device_health_page",
    "v1.device_health_list": "web:device_health_api_list",
    "v1.device_health_create": "web:device_health_api_create",
    "v1.device_health_update": "web:device_health_api_update",
    "v1.device_health_delete": "web:device_health_api_delete",
    "v1.device_health_enable": "web:device_health_api_enable",
    "v1.device_health_disable": "web:device_health_api_disable",
    "v1.device_health_events": "web:device_health_api_events",
    "v1.device_health_alerts": "web:device_health_api_alerts",
    "v1.device_health_test_ping": "web:device_health_api_test_ping",
    "v1.device_health_router_interfaces": "web:device_health_api_router_interfaces",
    "v1.device_health_live_apply_get": "web:device_health_page",
    "v1.device_health_live_apply_set": "web:device_health_api_live_apply",

    # ── system / settings / tokens / tools ──
    "v1.system_status": "web:system_status",
    "v1.system_diagnostics": "web:diagnostics",
    "v1.system_sync_list": "web:sync_list",
    "v1.system_sync_retry": "web:sync_retry",
    "v1.system_sync_cancel": "web:sync_cancel",
    "v1.system_reconcile": "web:reconcile_now",
    "v1.system_license_file": "web:license_file",
    "v1.system_admin_bridge_usage_report": "api.use",
    "v1.system_admin_bridge_capacity_status": "web:admin_bridge",
    "v1.system_admin_bridge_license_sync": "web:license_file_sync",
    # identity sync / backup upload / restore rewrite admins or data → owner
    "v1.system_admin_bridge_identity_sync": SUPER,
    "v1.system_admin_bridge_heartbeat": "api.use",
    "v1.system_admin_bridge_backup_upload_latest": SUPER,
    "v1.system_admin_bridge_restore_poll": SUPER,
    "v1.system_admin_bridge_restore_snapshot": SUPER,
    "v1.system_admin_bridge_restore_apply": SUPER,
    "v1.system_admin_bridge_service_activations_poll": "api.use",
    "v1.system_admin_bridge_events": "web:admin_bridge",
    "v1.settings_get": "web:settings_page",
    "v1.settings_patch": "web:settings_page",
    "v1.tokens_list": "web:tok_list",
    "v1.tokens_create": "web:tok_create",
    "v1.tokens_revoke": "web:tok_revoke",
    "v1.tools_set_speeds": "web:tool_set_speeds",
    "v1.tools_general_adjustments": "web:tool_general_adj",
    "v1.tools_test_auth": "web:tool_test_auth",
    "v1.tools_radius_log": "web:tool_radius_log",
    "v1.tools_maintenance_preview": "web:tool_maintenance",
    "v1.tools_maintenance_run": "web:tool_maintenance",

    # ── Business OS finance (wallets = real money) ──
    "v1.business_wallets_list": "web:business_finance_wallets",
    "v1.business_wallets_detail": "web:business_finance_wallets",
    "v1.business_wallet_transactions": "web:business_finance_wallets",
    "v1.business_wallets_create": "web:business_finance_wallets_create",
    "v1.business_wallets_credit": "web:business_finance_wallet_credit",
    "v1.business_wallets_debit": "web:business_finance_wallet_debit",
    "v1.business_ledger_list": "web:finance_ledger",
    # a ledger correction rewrites posted money like a void → owner only
    "v1.business_ledger_correction": "web:finance_ledger_void",
    "v1.business_revenue_list": "web:business_finance_revenue",
    "v1.business_events_list": "reports.finance",
    "v1.business_events_record": "reports.finance",
    "v1.business_price_snapshots_list": "reports.finance",
    "v1.business_price_snapshots_capture": "reports.finance",
    "v1.business_summary": "web:business_finance",
}


# ─────────────────────────── evaluation ────────────────────────────

def _method() -> str:
    m = (request.method or "GET").upper()
    return "GET" if m == "HEAD" else m


def _resolve(spec: Spec | None, method: str) -> Optional[str]:
    if isinstance(spec, dict):
        return spec.get(method) or spec.get("*")
    return spec


def _perms_of(admin) -> tuple[str, ...]:
    try:
        from ..radius.services.admins import get_admins_service
        return tuple(get_admins_service().permissions_of(admin))
    except Exception:  # noqa: BLE001 — same fallback as the web login
        return ()


def decide(name: str, method: str, admin, *, tenant_id: int) -> Optional[int]:
    """The guard decision for API endpoint ``name`` (``request.endpoint``
    without the ``api.`` prefix): ``None`` = allowed, else 403/429.
    ``admin`` is the (non-owner) admin behind the token."""
    if name in API_AUTH_ONLY:
        return None
    spec = _resolve(API_PERMISSIONS.get(name), method)
    if not spec:
        return 403                      # unmapped → deny by default
    if spec == SUPER:
        return 403                      # owner-like already bypassed
    perms = _perms_of(admin)
    if spec.startswith("web:"):
        from ..radius.routes.blueprint import rbac_denial_status
        return rbac_denial_status(spec[4:], method, is_super=False,
                                  perms=perms, admin_id=int(admin.id),
                                  tenant_id=tenant_id, record_activity=False)
    if spec.startswith("mt:"):
        from ..radius.services import mt_permissions
        return None if mt_permissions.has(admin, spec[3:]) else 403
    if spec.startswith("grant:"):
        try:
            from ..radius.services.manager_distributor_ops import (
                ManagerDistributorOpsService,
            )
            granted = ManagerDistributorOpsService(tenant_id=tenant_id).has_permission(
                entity_type="manager", entity_id=int(admin.id),
                permission=spec[6:])
        except Exception:  # noqa: BLE001 — never grant on a lookup error
            granted = False
        return None if granted else 403
    keys = [k for k in spec.split("|") if k]
    return None if any(k in perms for k in keys) else 403


# ─────────────────────── tools screen (app) ────────────────────────
#: The «الأدوات» screen of the app: tool key → (API endpoint, method, path,
#: Arabic label). ``tool_permissions`` evaluates each with the SAME decision
#: the guard takes, so the app hides exactly what the server would refuse
#: (set-speeds / test-auth / maintenance / general adjustments are owner-only).
TOOLS: dict[str, tuple[str, str, str, str]] = {
    "set_speeds": ("v1.tools_set_speeds", "POST", "/api/v1/tools/set-speeds",
                   "ضبط السرعات جماعيًّا"),
    "general_adjustments": ("v1.tools_general_adjustments", "POST",
                            "/api/v1/tools/general-adjustments", "تعديلات عامّة جماعيّة"),
    "test_auth": ("v1.tools_test_auth", "POST", "/api/v1/tools/test-auth",
                  "اختبار المصادقة"),
    "radius_log": ("v1.tools_radius_log", "GET", "/api/v1/tools/radius-log",
                   "سجلّ الراديوس"),
    "maintenance": ("v1.tools_maintenance_preview", "POST",
                    "/api/v1/tools/maintenance/preview", "الصيانة الجماعيّة"),
}


def tool_permissions(admin, *, tenant_id: int, owner: bool | None = None) -> dict[str, bool]:
    """``{tool_key: allowed}`` for ``admin`` — the guard's own decision per tool
    (owner / co-owner → all True). Never raises (False on an error)."""
    if owner is None:
        try:
            from ..radius.auth.owner import is_owner_like
            owner = bool(is_owner_like(admin))
        except Exception:  # noqa: BLE001
            owner = False
    out: dict[str, bool] = {}
    for key, (name, method, _path, _label) in TOOLS.items():
        if owner:
            out[key] = True
            continue
        try:
            out[key] = decide(name, method, admin, tenant_id=int(tenant_id)) is None
        except Exception:  # noqa: BLE001 — never grant on an error
            out[key] = False
    return out


def owner_only_tools() -> list[str]:
    """Tools whose guard spec is owner-only (``__super__`` or a web endpoint the
    web table reserves for the owner)."""
    out = []
    try:
        from ..radius.routes.blueprint import _PERM_GUARDED, _PERM_SUPER
    except Exception:  # noqa: BLE001
        return out
    for key, (name, method, _p, _l) in TOOLS.items():
        spec = _resolve(API_PERMISSIONS.get(name), method) or ""
        if spec == SUPER or (spec.startswith("web:")
                             and _PERM_GUARDED.get(spec[4:]) == _PERM_SUPER):
            out.append(key)
    return out


def api_permission_denial():
    """Called by ``enforce_api_auth`` once the credential is accepted.
    Returns an error response, or ``None`` to let the request through."""
    ep = request.endpoint or ""
    if not ep.startswith("api."):
        return None
    from .access_control import admin_id, tenant_id, token_admin
    if admin_id() <= 0:
        return None                     # unbound integration credential
    name = ep[4:]
    admin = token_admin()
    if admin is None or not getattr(admin, "enabled", False):
        return fail("forbidden", _DENIED_AR, status=403)
    from ..radius.auth.owner import is_owner_like
    if is_owner_like(admin):
        return None
    method = _method()
    code = decide(name, method, admin, tenant_id=tenant_id())
    if code is None:
        return None
    spec = _resolve(API_PERMISSIONS.get(name), method)
    details: dict[str, Any] = {"endpoint": name}
    if spec:
        details["requires"] = spec
    if code == 429:
        return fail("rate_limited", "بلغت الحدّ اليوميّ المسموح لهذا الإجراء.",
                    status=429, details=details)
    return fail("forbidden", _DENIED_AR if spec else _UNMAPPED_AR,
                status=403, details=details)


__all__ = ["API_PERMISSIONS", "API_AUTH_ONLY", "API_PUBLIC", "SUPER", "decide",
           "api_permission_denial", "TOOLS", "tool_permissions", "owner_only_tools"]
