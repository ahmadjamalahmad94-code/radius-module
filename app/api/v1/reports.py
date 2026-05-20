"""Customer service report API foundations."""
from __future__ import annotations

from flask import Blueprint

from ...radius.core.errors import RadiusValidationError
from ...radius.services.accounting import service_from_context
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    for slug, report_type in (
        ("sales", "daily"),
        ("sales/daily", "daily"),
        ("sales/monthly", "monthly"),
        ("sales/yearly", "yearly"),
        ("payments", "subscriber_payments"),
        ("loans", "loans"),
        ("activations", "activations"),
        ("card-sales", "card_sales"),
        ("profit-loss", "profit_loss"),
        ("distributor-debts", "distributor_debts"),
    ):
        bp.add_url_rule(
            f"/reports/{slug}",
            "reports_" + slug.replace("/", "_").replace("-", "_"),
            require_api_token(_report_view(report_type)),
            methods=["GET"],
        )

def _report_view(report_type: str):
    def _view():
        try:
            items = service_from_context().reports(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        return ok({"items": items, "count": len(items), "report_type": report_type})

    _view.__name__ = f"reports_{report_type}_view"
    return _view
