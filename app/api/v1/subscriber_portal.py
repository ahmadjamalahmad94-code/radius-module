"""Native subscriber portal API.

These endpoints are public self-service APIs. They intentionally do not depend
on the admin API token or an admin browser session.
"""
from __future__ import annotations

import functools
import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ...radius.core.errors import RadiusValidationError
from ...radius.db.connection import db, transaction
from ...radius.db.helpers import now_iso, parse_dt, row_to_dict
from ...radius.services.customer_portals import CustomerPortalService, PortalAuthError

TOKEN_TTL_SECONDS = 15 * 60
ERROR_STATUS = {
    "invalid_credentials": 401,
    "token_required": 401,
    "token_expired": 401,
    "forbidden": 403,
    "request_not_found": 404,
    "validation_error": 400,
    "rate_limited": 429,
}

_login_lock = Lock()
_login_hits: dict[str, deque[float]] = defaultdict(deque)


def register(bp: Blueprint) -> None:
    prefix = "/subscriber-portal"
    bp.add_url_rule(f"{prefix}/login", "subscriber_portal_login", login, methods=["POST"])
    bp.add_url_rule(f"{prefix}/logout", "subscriber_portal_logout", require_portal_token(logout), methods=["POST"])
    bp.add_url_rule(f"{prefix}/me", "subscriber_portal_me", require_portal_token(me), methods=["GET"])
    bp.add_url_rule(
        f"{prefix}/dashboard",
        "subscriber_portal_dashboard",
        require_portal_token(dashboard),
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{prefix}/requests",
        "subscriber_portal_requests",
        require_portal_token(requests_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{prefix}/requests/<int:request_id>",
        "subscriber_portal_request_detail",
        require_portal_token(request_detail),
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{prefix}/loan-request",
        "subscriber_portal_loan_request",
        require_portal_token(loan_request),
        methods=["POST"],
    )
    bp.add_url_rule(
        f"{prefix}/renewal-request",
        "subscriber_portal_renewal_request",
        require_portal_token(renewal_request),
        methods=["POST"],
    )


def _tenant_id() -> int:
    raw = request.headers.get("X-Tenant-Id") or request.args.get("tenant_id") or 1
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True) or {}
    return data if isinstance(data, dict) else {}


def _token() -> str:
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header.split(None, 1)[1].strip()
    return (request.headers.get("X-Subscriber-Portal-Token") or "").strip()


def _json_error(code: str, message: str = "", status: int | None = None):
    return jsonify({"ok": False, "error": code, "message": message or code}), status or ERROR_STATUS.get(code, 400)


def _json_result(payload: dict[str, Any], status: int = 200):
    return jsonify(payload), status


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _is_expired(value: Any) -> bool:
    dt = parse_dt(str(value)) if value else None
    return bool(dt and dt < _now())


def _login_rate_allowed(identity_key: str) -> bool:
    if current_app.testing:
        return True
    now = time.monotonic()
    with _login_lock:
        hits = _login_hits[identity_key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= 10:
            return False
        hits.append(now)
        return True


def _issue_token(*, tenant_id: int, subscriber: dict[str, Any]) -> str:
    raw_token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=TOKEN_TTL_SECONDS)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO customer_portal_tokens(
                tenant_id, token_hash, owner_type, owner_id, username,
                expires_at, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                tenant_id,
                _hash_token(raw_token),
                "subscriber",
                int(subscriber["id"]),
                str(subscriber.get("username") or ""),
                _iso(expires_at),
                now_iso(),
            ),
        )
    return raw_token


def _subscriber_from_token(token: str, *, tenant_id: int) -> dict[str, Any]:
    raw = str(token or "").strip()
    if not raw:
        raise PortalAuthError("token_required")
    row = db().execute(
        """
        SELECT *
        FROM customer_portal_tokens
        WHERE tenant_id=? AND token_hash=? AND owner_type='subscriber' AND revoked_at IS NULL
        LIMIT 1
        """,
        (tenant_id, _hash_token(raw)),
    ).fetchone()
    if not row:
        raise PortalAuthError("token_required")
    rec = row_to_dict(row)
    if _is_expired(rec.get("expires_at")):
        raise PortalAuthError("token_expired")
    db().execute("UPDATE customer_portal_tokens SET last_seen_at=? WHERE id=?", (now_iso(), int(rec["id"])))
    return CustomerPortalService(tenant_id=tenant_id).get_subscriber(int(rec["owner_id"]))


def _current_subscriber() -> dict[str, Any]:
    return _subscriber_from_token(_token(), tenant_id=_tenant_id())


def _capabilities() -> dict[str, bool]:
    return {
        "dashboard": True,
        "requests": True,
        "loan_request": True,
        "renewal_request": True,
        "support_request": True,
    }


def require_portal_token(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        try:
            _current_subscriber()
        except PortalAuthError as exc:
            code = str(exc) or "token_required"
            return _json_error(code, status=ERROR_STATUS.get(code, 401))
        return view(*args, **kwargs)

    return wrapped


def login():
    body = _body()
    username = str(body.get("username") or "").strip()
    client_key = f"{request.remote_addr or 'unknown'}:{username}"
    if not _login_rate_allowed(client_key):
        return _json_error("rate_limited", status=429)
    try:
        svc = CustomerPortalService(tenant_id=_tenant_id())
        subscriber = svc.authenticate_subscriber(
            username=username,
            password=str(body.get("password") or ""),
        )
    except PortalAuthError:
        return _json_error("invalid_credentials", "بيانات الدخول غير صحيحة.", 401)
    token = _issue_token(tenant_id=_tenant_id(), subscriber=subscriber)
    return _json_result(
        {
            "ok": True,
            "token": token,
            "expires_in": TOKEN_TTL_SECONDS,
            "subscriber": subscriber,
            "capabilities": _capabilities(),
        }
    )


def logout():
    db().execute(
        """
        UPDATE customer_portal_tokens
        SET revoked_at=?
        WHERE tenant_id=? AND token_hash=? AND owner_type='subscriber' AND revoked_at IS NULL
        """,
        (now_iso(), _tenant_id(), _hash_token(_token())),
    )
    return _json_result({"ok": True})


def me():
    subscriber = _current_subscriber()
    return _json_result({"ok": True, "subscriber": subscriber, "capabilities": _capabilities()})


def dashboard():
    subscriber = _current_subscriber()
    data = CustomerPortalService(tenant_id=_tenant_id()).subscriber_dashboard(int(subscriber["id"]))
    return _json_result({"ok": True, "dashboard": data, "capabilities": _capabilities()})


def requests_list():
    subscriber = _current_subscriber()
    items = CustomerPortalService(tenant_id=_tenant_id()).list_subscriber_requests(int(subscriber["id"]))
    return _json_result({"ok": True, "items": items})


def request_detail(request_id: int):
    subscriber = _current_subscriber()
    item = CustomerPortalService(tenant_id=_tenant_id()).get_subscriber_request(int(subscriber["id"]), int(request_id))
    if not item:
        return _json_error("request_not_found", "الطلب غير موجود.", 404)
    return _json_result({"ok": True, "item": item})


def loan_request():
    subscriber = _current_subscriber()
    body = _body()
    try:
        result = CustomerPortalService(tenant_id=_tenant_id()).submit_loan_request(
            subscriber_id=int(subscriber["id"]),
            requested_minutes=int(body.get("requested_minutes") or 0),
            reason=str(body.get("reason") or ""),
        )
    except (RadiusValidationError, ValueError) as exc:
        return _json_error("validation_error", str(exc) or "قيمة الطلب غير صحيحة.", 400)
    return _json_result({"ok": True, "request": result}, 201)


def renewal_request():
    subscriber = _current_subscriber()
    body = _body()
    result = CustomerPortalService(tenant_id=_tenant_id()).submit_renewal_request(
        subscriber_id=int(subscriber["id"]),
        reason=str(body.get("reason") or ""),
    )
    return _json_result({"ok": True, "request": result}, 201)
