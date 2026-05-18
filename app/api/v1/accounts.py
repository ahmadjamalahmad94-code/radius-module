"""
Accounts endpoints — REAL implementation.

كل endpoint يستدعي service حقيقي، يكتب في DB، ويُطلق sync لـ MT.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.core.types import Subscriber
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _serialize(sub: Subscriber) -> dict:
    d = asdict(sub)
    # ISO datetimes
    for k in ("first_login_at", "expire_at", "last_login_at", "last_seen_at",
              "created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat() + "Z"
    # لا نُسرّب password عبر API
    d.pop("password", None)
    return d


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/accounts", "accounts_list",
                    require_api_token(accounts_list), methods=["GET"])
    bp.add_url_rule("/accounts", "accounts_create",
                    require_api_token(accounts_create), methods=["POST"])
    bp.add_url_rule("/accounts/<username>", "accounts_get",
                    require_api_token(accounts_get), methods=["GET"])
    bp.add_url_rule("/accounts/<username>", "accounts_patch",
                    require_api_token(accounts_patch), methods=["PATCH"])
    bp.add_url_rule("/accounts/<username>", "accounts_delete",
                    require_api_token(accounts_delete), methods=["DELETE"])
    bp.add_url_rule("/accounts/<username>/reset_password", "accounts_reset_pw",
                    require_api_token(accounts_reset_pw), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/extend_time", "accounts_extend",
                    require_api_token(accounts_extend), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/disable", "accounts_disable",
                    require_api_token(accounts_disable), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/enable", "accounts_enable",
                    require_api_token(accounts_enable), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/usage", "accounts_usage",
                    require_api_token(accounts_usage), methods=["GET"])


def _svc():
    from ...radius.services.users import get_users_service
    return get_users_service()


# ─────────────── views ───────────────

def accounts_list():
    try:
        limit = min(int(request.args.get("limit") or 50), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    status = request.args.get("status")
    items = _svc().list(status=status, limit=limit, offset=offset)
    return ok({"items": [_serialize(s) for s in items], "count": len(items)})


def accounts_create():
    body = request.get_json(silent=True) or {}
    if not body.get("username") or not body.get("password"):
        return fail("validation_error", "username + password مطلوبان", status=422)
    plan_id = body.get("plan_id")
    expire_at = None
    if body.get("expire_at"):
        try: expire_at = datetime.fromisoformat(body["expire_at"].replace("Z", ""))
        except ValueError:
            return fail("validation_error", "expire_at format invalid", status=422)
    sub = Subscriber(
        id=None, tenant_id=_tid(),
        username=str(body["username"]).strip(),
        password=str(body["password"]),
        plan_id=int(plan_id) if plan_id else None,
        full_name=str(body.get("full_name", "")).strip(),
        mobile=str(body.get("mobile", "")).strip(),
        email=str(body.get("email", "")).strip(),
        beneficiary_ref=str(body.get("beneficiary_ref", "")).strip(),
        expire_at=expire_at,
        user_type=body.get("user_type", "subscriber"),
        status=body.get("status", "enabled"),
    )
    try:
        saved = _svc().create(actor=_actor(), sub=sub)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("conflict", e.message, status=409)
    return ok(_serialize(saved), status=201)


def accounts_get(username: str):
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", f"account {username} not found", status=404)
    return ok(_serialize(sub))


def accounts_patch(username: str):
    body = request.get_json(silent=True) or {}
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", "account not found", status=404)
    # حقول مسموحة بالتعديل عبر API
    patch: dict = {}
    for k in ("full_name", "mobile", "email", "remark", "status",
              "mac_lock", "static_ip", "plan_id", "beneficiary_ref"):
        if k in body:
            patch[k] = body[k]
    if "expire_at" in body and body["expire_at"]:
        try: patch["expire_at"] = datetime.fromisoformat(body["expire_at"].replace("Z", ""))
        except ValueError:
            return fail("validation_error", "expire_at format invalid", status=422)
    new_sub = replace(sub, **patch)
    try:
        _svc().update(actor=_actor(), sub=new_sub)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(_svc().get(username)))


def accounts_delete(username: str):
    try:
        _svc().delete(actor=_actor(), username=username)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": username})


def accounts_reset_pw(username: str):
    body = request.get_json(silent=True) or {}
    pw = body.get("new_password")
    if not pw:
        return fail("validation_error", "new_password مطلوب", status=422)
    try:
        _svc().reset_password(actor=_actor(), username=username, new_password=str(pw))
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"username": username, "reset": True})


def accounts_extend(username: str):
    body = request.get_json(silent=True) or {}
    try:
        minutes = int(body.get("minutes") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "minutes must be int", status=422)
    if minutes <= 0:
        return fail("validation_error", "minutes > 0 required", status=422)
    try:
        saved = _svc().extend_time(actor=_actor(), username=username, minutes=minutes)
    except RadiusNotFound:
        return fail("not_found", "account not found", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"username": username, "extended_minutes": minutes,
               "new_expire_at": saved.expire_at.isoformat() + "Z" if saved.expire_at else None})


def accounts_disable(username: str):
    try:
        _svc().disable(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "account not found", status=404)
    return ok({"username": username, "status": "disabled"})


def accounts_enable(username: str):
    try:
        _svc().enable(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "account not found", status=404)
    return ok({"username": username, "status": "enabled"})


def accounts_usage(username: str):
    """يُرجع snapshot استهلاك من DB."""
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", "account not found", status=404)
    return ok({
        "username": sub.username,
        "used_seconds": sub.used_seconds,
        "used_bytes_in": sub.used_bytes_in,
        "used_bytes_out": sub.used_bytes_out,
        "online_count": sub.online_count,
        "last_seen_at": sub.last_seen_at.isoformat() + "Z" if sub.last_seen_at else None,
        "expire_at": sub.expire_at.isoformat() + "Z" if sub.expire_at else None,
    })
