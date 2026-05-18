"""Cards endpoints — REAL: generate batch + revoke."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusValidationError
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/generate", "cards_generate",
                    require_api_token(cards_generate), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>", "cards_get",
                    require_api_token(cards_get), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke",
                    require_api_token(cards_revoke), methods=["POST"])


def cards_generate():
    body = request.get_json(silent=True) or {}
    plan_id = body.get("plan_id")
    count = body.get("count", 1)
    if not plan_id:
        return fail("validation_error", "plan_id مطلوب", status=422)
    if not isinstance(count, int) or count <= 0 or count > 2000:
        return fail("validation_error", "count must be 1..2000", status=422)
    from ...radius.services.cards import get_cards_service
    try:
        batch, cards = get_cards_service().generate_batch(
            actor=_actor(), plan_id=int(plan_id), count=count,
            username_prefix=str(body.get("username_prefix") or "").strip(),
            username_length=int(body.get("username_length") or 8),
            password_length=int(body.get("password_length") or 6),
            notes=str(body.get("notes") or "")[:300],
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "batch": {"id": batch.id, "batch_code": batch.batch_code,
                  "plan_id": batch.plan_id, "count": batch.count,
                  "generated": batch.generated},
        "cards": [{"id": c.id, "username": c.username, "password": c.password,
                    "expire_at": c.expire_at.isoformat() + "Z" if c.expire_at else None}
                   for c in cards],
    }, status=201)


def cards_get(card_id: int):
    from ...radius.db.repos import cards_repo
    items = cards_repo.list_cards(_tid(), limit=10_000)
    for c in items:
        if c.id == card_id:
            return ok({
                "id": c.id, "batch_id": c.batch_id, "plan_id": c.plan_id,
                "username": c.username, "password": c.password,
                "used": c.used, "revoked": c.revoked,
                "expire_at": c.expire_at.isoformat() + "Z" if c.expire_at else None,
                "first_used_at": c.first_used_at.isoformat() + "Z" if c.first_used_at else None,
            })
    return fail("not_found", "card not found", status=404)


def cards_revoke(card_id: int):
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().revoke_card(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"id": card_id, "revoked": True})
