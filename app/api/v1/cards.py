"""Cards endpoints — generate, list batches, drill-down, revoke.

The generation/revoke path goes through CardsService (audit + RADIUS sync
included). Read endpoints query the cards repo directly via the same
CardsStore helpers used by the web admin.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusValidationError
from ..access_control import batch_in_scope, current_distributor, deny_out_of_scope
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
    bp.add_url_rule("/cards/batches/<int:batch_id>", "cards_batch_update",
                    require_api_token(cards_batch_update), methods=["PATCH", "PUT"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/summary", "cards_batch_summary",
                    require_api_token(cards_batch_summary), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards", "cards_of_batch",
                    require_api_token(cards_of_batch), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>", "cards_get",
                    require_api_token(cards_get), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke",
                    require_api_token(cards_revoke), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/enable", "cards_enable",
                    require_api_token(cards_enable), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/disable", "cards_disable",
                    require_api_token(cards_disable), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/lock-mac", "cards_lock_mac",
                    require_api_token(cards_lock_mac), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/unlock-mac", "cards_unlock_mac",
                    require_api_token(cards_unlock_mac), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/reset-usage", "cards_reset_usage",
                    require_api_token(cards_reset_usage), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/disconnect", "cards_disconnect",
                    require_api_token(cards_disconnect), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/delete-permanent", "cards_delete_permanent",
                    require_api_token(cards_delete_permanent), methods=["POST"])


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
        "assigned_to": b.assigned_to or None,
        "distributor_id": b.distributor_id,
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
        "include_batch_number": b.include_batch_number,
        "password_generation_type": b.password_generation_type,
        "random_generation_enabled": b.random_generation_enabled,
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
        "duration_mode": b.duration_mode,
        "count_by_seconds": b.count_by_seconds,
        "count_from_first_connect": b.count_from_first_connect,
        "on_quota_exhaust": b.on_quota_exhaust,
        "auto_renew_after_first_use": b.auto_renew_after_first_use,
        "transfer_to_student_status_on_connect": b.transfer_to_student_status_on_connect,
        "close_user_session_on_disconnect": b.close_user_session_on_disconnect,
        "allow_entry_by_previous_card_palestine": b.allow_entry_by_previous_card_palestine,
        "switch_to_mac_on_connect": b.switch_to_mac_on_connect,
        "lock_to_mac_on_close": b.lock_to_mac_on_close,
        "phone_only_login": b.phone_only_login,
        "metadata": b.metadata,
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


def _card_or_response(card_id: int):
    from ...radius.db.repos import cards_repo

    card = cards_repo.get_card(_tid(), card_id)
    if not card:
        return None, fail("not_found", "card not found", status=404)
    if not batch_in_scope(int(card.batch_id or 0)):
        return None, deny_out_of_scope()
    return card, None


def _updated_card_payload(username: str, *, action: str) -> dict:
    from ...radius.services.card_checker import check_card

    return {
        "action": action,
        "card": check_card(_tid(), username),
    }


def _body() -> dict:
    body = request.get_json(silent=True) or {}
    return body if isinstance(body, dict) else {}


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
            password_charset=str(body.get("password_charset") or "digits"),
            password_generation_type=str(body.get("password_generation_type") or "medium"),
            include_batch_number=bool(body.get("include_batch_number")),
            random_generation_enabled=body.get("random_generation_enabled") is not False,
            time_value=int(body.get("time_value") or 0),
            time_unit=str(body.get("time_unit") or "days"),
            device_count=int(body.get("device_count") or 1),
            duration_mode=str(body.get("duration_mode") or "time_unit"),
            validity_after_first_login_days=int(body.get("validity_after_first_login_days") or 0),
            count_by_seconds=bool(body.get("count_by_seconds")),
            count_from_first_connect=body.get("count_from_first_connect") is not False,
            on_quota_exhaust=str(body.get("on_quota_exhaust") or "stop"),
            auto_renew_after_first_use=bool(body.get("auto_renew_after_first_use")),
            transfer_to_student_status_on_connect=bool(body.get("transfer_to_student_status_on_connect")),
            close_user_session_on_disconnect=bool(body.get("close_user_session_on_disconnect")),
            allow_entry_by_previous_card_palestine=bool(body.get("allow_entry_by_previous_card_palestine")),
            switch_to_mac_on_connect=bool(body.get("switch_to_mac_on_connect")),
            lock_to_mac_on_close=bool(body.get("lock_to_mac_on_close")),
            phone_only_login=bool(body.get("phone_only_login")),
            price_per_card=float(body.get("price_per_card") or 0),
            price_bulk=float(body.get("price_bulk") or 0),
            total_price=float(body.get("total_price") or 0),
            total_quota_mb=int(body.get("total_quota_mb") or 0),
            package_name=str(body.get("package_name") or "").strip(),
            service_name=str(body.get("service_name") or "").strip(),
            manager_id=int(body.get("manager_id") or 0),
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
    dist = current_distributor()
    if dist:
        items = [b for b in items if int(b.distributor_id or 0) == int(dist["id"])]
    return ok({"items": [_serialize_batch(b) for b in items], "count": len(items)})


def cards_batch_get(batch_id: int):
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
    from ...radius.db.repos import cards_repo
    batch = cards_repo.get_batch(_tid(), batch_id)
    if not batch:
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    return ok(_serialize_batch(batch))


def cards_batch_update(batch_id: int):
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("validation_error", "JSON body must be an object", status=422)
    from ...radius.services.cards import get_cards_service
    try:
        batch = get_cards_service().update_batch(
            actor=_actor(),
            batch_id=batch_id,
            data=body,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"batch": _serialize_batch(batch)})


def cards_batch_summary(batch_id: int):
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
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
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
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
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().revoke_card(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    payload = _updated_card_payload(card.username, action="revoke")
    payload.update({"id": card_id, "revoked": True})
    return ok(payload)


def cards_enable(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().enable_card(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="enable"))


def cards_disable(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    reason = str(_body().get("reason") or "")[:300]
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().disable_card(actor=_actor(), card_id=card_id, reason=reason)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="disable"))


def cards_lock_mac(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    mac = str(_body().get("mac") or "").strip()[:64]
    if not mac:
        return fail("validation_error", "mac is required", status=422)
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().lock_card_mac(actor=_actor(), card_id=card_id, mac=mac)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="lock_mac"))


def cards_unlock_mac(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().unlock_card_mac(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="unlock_mac"))


def cards_reset_usage(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().reset_card_usage(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="reset_usage"))


def cards_disconnect(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    body = _body()
    session_id = str(body.get("session_id") or "")
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().disconnect_card(
            actor=_actor(),
            username=card.username,
            session_id=session_id,
        )
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="disconnect"))


def cards_delete_permanent(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    body = _body()
    confirm = str(body.get("confirm") or "")
    if confirm != f"DELETE:{card.username}":
        return fail(
            "validation_error",
            "confirm must be DELETE:<username>",
            status=422,
        )
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().delete_card_permanently(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "action": "delete_permanent",
        "card": {
            "exists": False,
            "status": "deleted",
            "username": card.username,
            "id": card_id,
        },
    })
