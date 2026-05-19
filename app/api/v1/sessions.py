"""Sessions endpoints: online users list, search, state enrichment, disconnect."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, g, request

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
        for key in ("username", "mac_address", "framed_ip", "nas_address", "session_id")
    )


def _enrich_session(item: dict) -> dict:
    from ...radius.db.repos import subscribers_repo
    from ...radius.services.operations import classify_online_state

    sub = subscribers_repo.get_subscriber(_tid(), item.get("username") or "")
    item.update(classify_online_state(
        account_status=sub.status if sub else "",
        expire_at=sub.expire_at if sub else None,
        is_online=True,
    ))
    item["account_status"] = sub.status if sub else None
    item["subscriber_id"] = sub.id if sub else None
    item["expires_at"] = sub.expire_at.isoformat() + "Z" if sub and sub.expire_at else None
    return item


def sessions_online():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    if len(query) > 80:
        return fail("validation_error", "query is too long", status=422)

    items = []
    for session in _svc().list(limit=500):
        data = asdict(session)
        for key in ("started_at", "last_update_at"):
            value = data.get(key)
            if hasattr(value, "isoformat"):
                data[key] = value.isoformat() + "Z"
        enriched = _enrich_session(data)
        if _matches_query(enriched, query):
            items.append(enriched)

    states: dict[str, int] = {}
    for item in items:
        states[item["state"]] = states.get(item["state"], 0) + 1
    return ok({"items": items, "count": len(items), "states": states, "query": query})


def sessions_disconnect():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username:
        return fail("validation_error", "username مطلوب", status=422)
    session_id = body.get("session_id")
    try:
        _svc().disconnect(actor=_actor(), username=username, session_id=session_id)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({"username": username, "session_id": session_id, "disconnect_requested": True})
