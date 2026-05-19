"""Cards endpoints — generate, list batches, drill-down, revoke.

The generation/revoke path goes through CardsService (audit + RADIUS sync
included). Read endpoints query the cards repo directly via the same
CardsStore helpers used by the web admin.
"""
from __future__ import annotations

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
    bp.add_url_rule("/cards/batches", "cards_batches_list",
                    require_api_token(cards_batches_list), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>", "cards_batch_get",
                    require_api_token(cards_batch_get), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/summary", "cards_batch_summary",
                    require_api_token(cards_batch_summary), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards", "cards_of_batch",
                    require_api_token(cards_of_batch), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>", "cards_get",
                    require_api_token(cards_get), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke",
                    require_api_token(cards_revoke), methods=["POST"])


# ─────────────── helpers ───────────────

def _serialize_batch(b) -> dict:
    """Stable JSON shape — converts datetimes + drops nothing sensitive."""
    return {
        "id": b.id,
        "tenant_id": b.tenant_id,
        "batch_code": b.batch_code,
        "package_name": b.package_name,
        "plan_id": b.plan_id,
        "count": b.count,
        "generated": b.generated,
        "used": b.used,
        "status": b.status,
        "deleted_at": b.deleted_at.isoformat() + "Z" if b.deleted_at else None,
        "deleted_by": b.deleted_by or None,
        "delete_reason": b.delete_reason or None,
        "expire_at": b.expire_at.isoformat() + "Z" if b.expire_at else None,
        "created_at": b.created_at.isoformat() + "Z" if b.created_at else None,
        "created_by": b.created_by,
        "notes": b.notes,
        "service_name": b.service_name,
        "username_prefix": b.username_prefix,
        "username_suffix": b.username_suffix,
        "username_length": b.username_length,
        "password_length": b.password_length,
        "password_charset": b.password_charset,
        "password_generation_type": b.password_generation_type,
        "starts_with_or_ends_with": b.starts_with_or_ends_with,
        "prefix_or_suffix_value": b.prefix_or_suffix_value,
        "time_value": b.time_value,
        "time_unit": b.time_unit,
        "device_count": b.device_count,
        "validity_after_first_login_days": b.validity_after_first_login_days,
        "price_per_card": b.price_per_card,
        "price_bulk": b.price_bulk,
        "total_price": b.total_price,
        "total_quota_mb": b.total_quota_mb,
    }


def _serialize_card(c) -> dict:
    return {
        "id": c.id,
        "batch_id": c.batch_id,
        "plan_id": c.plan_id,
        "username": c.username,
        "password": c.password,
        "used": c.used,
        "revoked": c.revoked,
        "expire_at": c.expire_at.isoformat() + "Z" if c.expire_at else None,
        "first_used_at": c.first_used_at.isoformat() + "Z" if c.first_used_at else None,
        "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
    }


# ─────────────── views ───────────────

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
            username_suffix=str(body.get("username_suffix") or "").strip(),
            starts_with_or_ends_with=str(body.get("starts_with_or_ends_with") or "").strip(),
            prefix_or_suffix_value=str(body.get("prefix_or_suffix_value") or "").strip(),
            username_length=int(body.get("username_length") or 8),
            password_length=int(body.get("password_length") or 6),
            password_generation_type=str(body.get("password_generation_type") or "medium"),
            time_value=int(body.get("time_value") or 0),
            time_unit=str(body.get("time_unit") or "days"),
            device_count=int(body.get("device_count") or 1),
            notes=str(body.get("notes") or "")[:300],
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "batch": _serialize_batch(batch),
        "cards": [_serialize_card(c) for c in cards],
    }, status=201)


def cards_batches_list():
    try:
        limit = min(int(request.args.get("limit") or 100), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    from ...radius.services.cards import get_cards_service
    items = get_cards_service().list_batches(limit=limit, offset=offset)
    return ok({"items": [_serialize_batch(b) for b in items], "count": len(items)})


def cards_batch_get(batch_id: int):
    from ...radius.db.repos import cards_repo
    batch = cards_repo.get_batch(_tid(), batch_id)
    if not batch:
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    return ok(_serialize_batch(batch))


def cards_batch_summary(batch_id: int):
    from ...radius.db.repos import cards_repo
    summary = cards_repo.batch_operational_summary(_tid(), batch_id)
    if not summary:
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    return ok({"summary": summary})


def cards_of_batch(batch_id: int):
    """List cards belonging to a batch with optional used/revoked filters
    and pagination."""
    try:
        limit = min(int(request.args.get("limit") or 200), 2000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    used = request.args.get("used")
    revoked = request.args.get("revoked")
    used_bool = None if used is None else used.lower() in ("1", "true", "yes")
    revoked_bool = None if revoked is None else revoked.lower() in ("1", "true", "yes")

    from ...radius.db.repos import cards_repo
    if not cards_repo.get_batch(_tid(), batch_id):
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    items = cards_repo.list_cards(
        _tid(),
        batch_id=batch_id,
        used=used_bool,
        revoked=revoked_bool,
        limit=limit,
        offset=offset,
    )
    return ok({
        "batch_id": batch_id,
        "items": [_serialize_card(c) for c in items],
        "count": len(items),
    })


def cards_get(card_id: int):
    from ...radius.db.repos import cards_repo
    items = cards_repo.list_cards(_tid(), limit=10_000)
    for c in items:
        if c.id == card_id:
            return ok(_serialize_card(c))
    return fail("not_found", "card not found", status=404)


def cards_revoke(card_id: int):
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().revoke_card(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"id": card_id, "revoked": True})
