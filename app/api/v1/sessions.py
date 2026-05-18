"""Sessions endpoints — online sessions + disconnect."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/sessions/online", "sessions_online",
                    require_api_token(sessions_online), methods=["GET"])
    bp.add_url_rule("/sessions/disconnect", "sessions_disconnect",
                    require_api_token(sessions_disconnect), methods=["POST"])


def _adapter():
    from app.radius.integration.factory import get_radius_adapter
    return get_radius_adapter()


def sessions_online():
    items = []
    for s in _adapter().list_online(limit=500):
        d = asdict(s)
        # تحويل datetimes لـ isoformat
        for k in ("started_at", "last_update_at"):
            v = d.get(k)
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat() + "Z"
        items.append(d)
    return ok({"items": items, "count": len(items)})


def sessions_disconnect():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username:
        return fail("validation_error", "username مطلوب", status=422)
    session_id = body.get("session_id")
    try:
        _adapter().disconnect(username, session_id=session_id)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({"username": username, "session_id": session_id, "disconnect_requested": True})
