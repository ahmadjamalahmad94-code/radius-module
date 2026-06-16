"""
HobeRadius API v1.

كل resource في ملف مستقل (≤ 200 سطر) ويُسجَّل عبر register_v1.
"""
from __future__ import annotations

from flask import Blueprint


def register_v1(parent: Blueprint) -> None:
    v1 = Blueprint("v1", __name__, url_prefix="/v1")

    from . import (
        accounting, accounts, admins, audit, backups, bandwidth_profiles, bandwidth_schedules, business_os,
        contracts, events,
        card_checker, card_users, cards, communications, customer_portals, dashboard, devices, distributors, health, internal_auth,
        hotspot_cards, invoices, ledger, lifecycle, loans, mikrotik, mikrotik_control, nas, network_devices, network_policy,
        operational_reports, network_telegram, reports_login_states,
        payments, pools, print_templates, profiles, recycle_bin, reports, router_alerts, router_metrics, service_requests, services, sessions,
        settings, share_groups, site_exit, store, subscriber_groups, system, tenants, tickets, tokens, tools, vouchers, webhooks, whatsapp,
        setup_wizard, subscriber_portal, whatsapp_bot,
    )
    health.register(v1)
    accounts.register(v1)
    cards.register(v1)
    card_users.register(v1)
    hotspot_cards.register(v1)
    subscriber_portal.register(v1)
    card_checker.register(v1)
    communications.register(v1)
    whatsapp.register(v1)
    customer_portals.register(v1)
    # متجر المايكروتيك — نقاط عامة لمستخدمي البطاقات (توكن موقّع
    # خاص بها، ليست خلف require_api_token الإداري).
    store.register(v1)
    loans.register(v1)
    recycle_bin.register(v1)
    ledger.register(v1)
    lifecycle.register(v1)
    payments.register(v1)
    distributors.register(v1)
    reports.register(v1)
    reports_login_states.register(v1)
    operational_reports.register(v1)
    bandwidth_profiles.register(v1)
    bandwidth_schedules.register(v1)
    print_templates.register(v1)
    backups.register(v1)
    profiles.register(v1)
    nas.register(v1)
    network_devices.register(v1)
    router_alerts.register(v1)
    router_metrics.register(v1)
    sessions.register(v1)
    accounting.register(v1)
    webhooks.register(v1)
    setup_wizard.register(v1)
    mikrotik.register(v1)
    mikrotik_control.register(v1)  # K3: system/*  · K4: interfaces + network
    internal_auth.register(v1)
    dashboard.register(v1)
    admins.register(v1)
    audit.register(v1)
    devices.register(v1)
    pools.register(v1)
    vouchers.register(v1)
    invoices.register(v1)
    tickets.register(v1)
    service_requests.register(v1)
    services.register(v1)
    share_groups.register(v1)
    subscriber_groups.register(v1)
    site_exit.register(v1)
    events.register(v1)
    network_telegram.register(v1)
    whatsapp_bot.register(v1)
    system.register(v1)
    settings.register(v1)
    tokens.register(v1)
    tenants.register(v1)
    tools.register(v1)
    network_policy.register(v1)
    business_os.register(v1)
    contracts.register(v1)

    # introspection — مفيد للـ HobeHub لاكتشاف ما هو متاح
    from ..auth import require_api_token
    @v1.get("/_routes")
    @require_api_token
    def _routes_list():
        from ..responses import ok
        from flask import current_app
        items = [
            {
                "rule": r.rule,
                "methods": sorted(r.methods - {"HEAD", "OPTIONS"}),
                "endpoint": r.endpoint,
            }
            for r in current_app.url_map.iter_rules()
            if r.endpoint.startswith("api.v1.")
        ]
        items.sort(key=lambda x: x["rule"])
        return ok({"routes": items})

    parent.register_blueprint(v1)
