"""Customer portal navigation API for native operator clients."""
from __future__ import annotations
from app.i18n_text import _tr

from flask import Blueprint

from ..auth import require_api_token
from ..responses import ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/customer-portals",
        "customer_portals_overview",
        require_api_token(customer_portals_overview),
        methods=["GET"],
    )


def customer_portals_overview():
    return ok(
        {
            "items": [
                {
                    "key": "subscriber_portal",
                    "label": _tr("بوابة المشترك"),
                    "description": _tr("دخول المشترك لعرض الاشتراك والاستخدام والطلبات والمدفوعات."),
                    "public_path": "/portal/subscriber/login",
                    "admin_path": "/admin/radius/portal/subscriber/login",
                    "home_path": "/portal/subscriber",
                    "available_actions": ["login", "loan_request", "renewal_request"],
                    "security_note": _tr("المشترك يرى بياناته فقط، ولا تظهر له قوائم الإدارة."),
                },
                {
                    "key": "card_user_portal",
                    "label": _tr("بوابة مستخدم البطاقة"),
                    "description": _tr("دخول مستخدم البطاقة لشحن المحفظة وشراء الكروت الذاتية."),
                    "public_path": "/portal/card/login",
                    "admin_path": "/admin/radius/portal/card/login",
                    "home_path": "/portal/card",
                    "available_actions": ["login", "redeem_card", "purchase_card"],
                    "security_note": _tr("كلمة مرور البوابة لا ترجع في API، والعمليات تتم داخل بوابة الكرت."),
                },
            ],
            "security": {
                "summary": _tr("هذه الواجهة تعرض روابط وإرشادات فقط ولا تنفذ شراء أو تجديد أو سلفة."),
                "admin_navigation_only": True,
                "uses_existing_portal_sessions": True,
            },
        }
    )
