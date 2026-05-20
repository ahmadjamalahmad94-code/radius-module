"""Sessions endpoints: online users list, search, state enrichment, disconnect."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from flask import Blueprint, g, request

from ..access_control import deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/sessions/online", "sessions_online",
                    require_api_token(sessions_online), methods=["GET"])
    bp.add_url_rule("/sessions/disconnect", "sessions_disconnect",
                    require_api_token(sessions_disconnect), methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.sessions import get_online_sessions_service
    return get_online_sessions_service()


def _matches_query(item: dict, query: str) -> bool:
    if not query:
        return True
    q = query.lower()
    return any(
        q in str(item.get(key) or "").lower()
        for key in (
            "username", "mac_address", "framed_ip", "nas_address",
            "session_id", "user_type", "state",
        )
    )


def _enrich_session(item: dict) -> dict:
    from ...radius.db.repos import cards_repo, subscribers_repo
    from ...radius.services.operations import classify_online_state

    username = item.get("username") or ""
    card = cards_repo.get_card_by_username(_tid(), username)
    sub = subscribers_repo.get_subscriber(_tid(), username)
    is_card = card is not None
    expire_at = card.expire_at if is_card else (sub.expire_at if sub else None)
    account_status = (
        "revoked" if is_card and getattr(card, "revoked", False)
        else (sub.status if sub else "active")
    )
    item.update(classify_online_state(
        account_status=account_status,
        expire_at=expire_at,
        is_online=True,
    ))
    item["account_status"] = sub.status if sub else None
    item["subscriber_id"] = sub.id if sub else None
    item["card_id"] = card.id if card else None
    item["card_batch_id"] = card.batch_id if card else None
    item["user_type"] = "card" if is_card else "subscriber"
    item["user_type_label"] = "بطاقة" if is_card else "مشترك"
    item["expires_at"] = expire_at.isoformat() + "Z" if expire_at else None

    # Backward-compatible aliases for older mobile clients and clearer JSON.
    item["nas_ip_address"] = item.get("nas_address") or ""
    item["framed_ip_address"] = item.get("framed_ip") or ""
    item["calling_station_id"] = item.get("mac_address") or ""
    item["called_station_id"] = item.get("called_station_id") or ""
    item["nas_port_id"] = item.get("nas_port_id") or item.get("nas_id") or ""

    started_at = item.get("started_at")
    last_update_at = item.get("last_update_at") or datetime.utcnow()
    if hasattr(started_at, "replace") and hasattr(last_update_at, "replace"):
        try:
            item["session_time"] = max(0, int((last_update_at - started_at).total_seconds()))
        except TypeError:
            item["session_time"] = 0
    else:
        item["session_time"] = 0
    return item


def sessions_online():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    kind = (request.args.get("type") or request.args.get("kind") or "all").strip().lower()
    aliases = {
        "": "all",
        "all": "all",
        "subscriber": "subscriber",
        "subscribers": "subscriber",
        "user": "subscriber",
        "users": "subscriber",
        "card": "card",
        "cards": "card",
    }
    kind = aliases.get(kind, kind)
    if kind not in {"all", "subscriber", "card"}:
        return fail("validation_error", "type must be all, subscriber, or card", status=422)
    if len(query) > 80:
        return fail("validation_error", "query is too long", status=422)

    items = []
    for session in _svc().list(limit=500):
        data = asdict(session)
        enriched = _enrich_session(data)
        for key in ("started_at", "last_update_at"):
            value = enriched.get(key)
            if hasattr(value, "isoformat"):
                enriched[key] = value.isoformat() + "Z"
        if not subscriber_in_scope(username=enriched.get("username") or ""):
            continue
        if kind != "all" and enriched.get("user_type") != kind:
            continue
        if _matches_query(enriched, query):
            items.append(enriched)

    states: dict[str, int] = {}
    types: dict[str, int] = {"subscriber": 0, "card": 0}
    for item in items:
        states[item["state"]] = states.get(item["state"], 0) + 1
        user_type = item.get("user_type") or "subscriber"
        types[user_type] = types.get(user_type, 0) + 1
    return ok({
        "items": items,
        "count": len(items),
        "states": states,
        "types": types,
        "query": query,
        "type": kind,
    })


def sessions_disconnect():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username:
        return fail("validation_error", "username مطلوب", status=422)
    if not subscriber_in_scope(username=username):
        return deny_out_of_scope()
    session_id = body.get("session_id")
    try:
        _svc().disconnect(actor=_actor(), username=username, session_id=session_id)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({"username": username, "session_id": session_id, "disconnect_requested": True})
