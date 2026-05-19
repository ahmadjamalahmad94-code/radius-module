"""Payments and partial payment API foundation."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.services.accounting import service_from_context
from ..auth import require_api_token
from ..responses import fail, ok


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/payments", "payments_list",
                    require_api_token(payments_list), methods=["GET"])
    bp.add_url_rule("/payments", "payments_create",
                    require_api_token(payments_create), methods=["POST"])
    bp.add_url_rule("/payments/<int:payment_id>/void",
                    "payments_void", methods=["POST"],
                    view_func=require_api_token(payments_void))


def payments_list():
    try:
        limit = min(int(request.args.get("limit") or 100), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
        subscriber_id = request.args.get("subscriber_id")
        items = service_from_context().list_payments(
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=limit,
            offset=offset,
        )
    except (ValueError, RadiusValidationError) as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"items": items, "count": len(items)})


def payments_create():
    body = request.get_json(silent=True) or {}
    try:
        payment = service_from_context().create_payment(body, actor=_actor())
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422, details=e.details)
    return ok({"payment": payment}, status=201)


def payments_void(payment_id: int):
    return fail(
        "not_implemented",
        "Payment voids require a dedicated reversal slice; use /ledger/void for now.",
        status=501,
        details={"payment_id": payment_id},
    )
