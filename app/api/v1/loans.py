"""Subscriber loans / credit API."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.services.accounting import service_from_context
from ..auth import require_api_token
from ..responses import fail, ok


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/loans", "loans_list",
                    require_api_token(loans_list), methods=["GET"])
    bp.add_url_rule("/loans", "loans_create",
                    require_api_token(loans_create), methods=["POST"])
    bp.add_url_rule("/loans/<int:loan_id>", "loans_get",
                    require_api_token(loans_get), methods=["GET"])
    bp.add_url_rule("/loans/<int:loan_id>/settle", "loans_settle",
                    require_api_token(loans_settle), methods=["POST"])


def loans_list():
    try:
        limit = min(int(request.args.get("limit") or 100), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
        subscriber_id = request.args.get("subscriber_id")
        items = service_from_context().list_loans(
            status=(request.args.get("status") or "").strip(),
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=limit,
            offset=offset,
        )
    except (ValueError, RadiusValidationError) as e:
        return fail("validation_error", getattr(e, "message", str(e)), status=422)
    return ok({"items": items, "count": len(items)})


def loans_create():
    body = request.get_json(silent=True) or {}
    try:
        loan = service_from_context().create_loan(body, actor=_actor())
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422, details=e.details)
    return ok({"loan": loan}, status=201)


def loans_get(loan_id: int):
    try:
        loan = service_from_context().get_loan(loan_id)
    except RadiusValidationError as e:
        return fail("not_found", e.message, status=404)
    return ok({"loan": loan})


def loans_settle(loan_id: int):
    body = request.get_json(silent=True) or {}
    try:
        settlement = service_from_context().settle_loan(
            loan_id,
            body,
            actor=_actor(),
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422, details=e.details)
    return ok({"settlement": settlement}, status=201)
