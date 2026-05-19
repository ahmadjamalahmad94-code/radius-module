"""
HobeRadius API v1.

كل resource في ملف مستقل (≤ 200 سطر) ويُسجَّل عبر register_v1.
"""
from __future__ import annotations

from flask import Blueprint


def register_v1(parent: Blueprint) -> None:
    v1 = Blueprint("v1", __name__, url_prefix="/v1")

    from . import (
        accounting, accounts, admins, audit, backups, bandwidth_schedules,
        card_checker, cards, dashboard, distributors, health, internal_auth,
        ledger, loans, mikrotik, nas, payments, print_templates, profiles,
        recycle_bin, reports, sessions, webhooks,
    )
    health.register(v1)
    accounts.register(v1)
    cards.register(v1)
    card_checker.register(v1)
    loans.register(v1)
    recycle_bin.register(v1)
    ledger.register(v1)
    payments.register(v1)
    distributors.register(v1)
    reports.register(v1)
    bandwidth_schedules.register(v1)
    print_templates.register(v1)
    backups.register(v1)
    profiles.register(v1)
    nas.register(v1)
    sessions.register(v1)
    accounting.register(v1)
    webhooks.register(v1)
    mikrotik.register(v1)
    internal_auth.register(v1)
    dashboard.register(v1)
    admins.register(v1)
    audit.register(v1)

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
