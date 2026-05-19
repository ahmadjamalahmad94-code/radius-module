"""Card Checker API endpoint."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.card_checker import check_card
from ..auth import require_api_token
from ..responses import fail, ok

_MAX_QUERY_LENGTH = 128


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/cards/check",
        "cards_check",
        require_api_token(cards_check),
        methods=["GET"],
    )


def cards_check():
    query = (request.args.get("query") or "").strip()
    if not query:
        return fail("validation_error", "query مطلوب", status=422)
    if len(query) > _MAX_QUERY_LENGTH:
        return fail(
            "validation_error",
            f"query must be {_MAX_QUERY_LENGTH} characters or less",
            status=422,
        )
    return ok({"card": check_card(_tid(), query)})
