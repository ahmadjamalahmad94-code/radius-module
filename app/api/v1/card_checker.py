"""Card Checker API endpoint."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.card_checker import check_card
from ..access_control import batch_in_scope, deny_out_of_scope
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
    """GET /cards/check?query=<username> — the card USERNAME always wins.

    The numeric card id is used only when asked for explicitly:
    ``?card_id=<n>`` or ``?query=id:<n>``. (It used to match ``username OR
    id``, so a short numeric query could open another card.)"""
    query = (request.args.get("query") or "").strip()
    raw_id = (request.args.get("card_id") or "").strip()
    if raw_id:
        if not raw_id.isdigit():
            return fail("validation_error", "معرّف البطاقة (card_id) يجب أن يكون رقمًا صحيحًا.",
                        status=422)
        card = check_card(_tid(), query or f"id:{raw_id}", card_id=int(raw_id))
        return _scoped(card)
    if not query:
        return fail("validation_error", "عبارة البحث مطلوبة.", status=422)
    if len(query) > _MAX_QUERY_LENGTH:
        return fail(
            "validation_error",
            f"عبارة البحث يجب ألا تتجاوز {_MAX_QUERY_LENGTH} حرفًا.",
            status=422,
        )
    card = check_card(_tid(), query)
    return _scoped(card)


def _scoped(card: dict):
    batch_id = ((card.get("batch") or {}).get("id") if card.get("exists") else None)
    if batch_id and not batch_in_scope(int(batch_id)):
        return deny_out_of_scope()
    return ok({"card": card})
