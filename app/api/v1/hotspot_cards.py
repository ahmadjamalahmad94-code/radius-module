"""Hotspot Electronic Cards Portal API.

These endpoints are public portal APIs. They intentionally do not depend on an
admin session or the management API token.
"""
from __future__ import annotations

import functools
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ...radius.services.hotspot_cards_portal import (
    ERROR_STATUS,
    HotspotCardsPortalError,
    HotspotCardsPortalService,
)

_login_lock = Lock()
_login_hits: dict[str, deque[float]] = defaultdict(deque)


def register(bp: Blueprint) -> None:
    prefix = "/hotspot/cards"
    bp.add_url_rule(f"{prefix}/login", "hotspot_cards_login", login, methods=["POST"])
    bp.add_url_rule(f"{prefix}/me", "hotspot_cards_me", require_portal_token(me), methods=["GET"])
    bp.add_url_rule(f"{prefix}/catalog", "hotspot_cards_catalog", require_portal_token(catalog), methods=["GET"])
    bp.add_url_rule(f"{prefix}/my-cards", "hotspot_cards_my_cards", require_portal_token(my_cards), methods=["GET"])
    bp.add_url_rule(f"{prefix}/purchase", "hotspot_cards_purchase", require_portal_token(purchase), methods=["POST"])
    bp.add_url_rule(f"{prefix}/send-sms", "hotspot_cards_send_sms", require_portal_token(send_sms), methods=["POST"])


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
    return (request.headers.get("X-Hotspot-Portal-Token") or "").strip()


def _json_error(code: str, message: str = "", status: int | None = None):
    return jsonify({"ok": False, "error": code, "message": message or code}), status or ERROR_STATUS.get(code, 400)


def _json_result(payload: dict[str, Any], status: int = 200):
    return jsonify(payload), status


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


def require_portal_token(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        token = _token()
        if not token:
            return _json_error("token_required", status=401)
        try:
            HotspotCardsPortalService(tenant_id=_tenant_id()).identity_from_token(token)
        except HotspotCardsPortalError as exc:
            return _json_error(exc.code, exc.message, exc.status)
        return view(*args, **kwargs)
    return wrapped


def login():
    body = _body()
    username = str(body.get("username") or "").strip()
    client_key = f"{request.remote_addr or 'unknown'}:{username}"
    if not _login_rate_allowed(client_key):
        return _json_error("rate_limited", status=429)
    try:
        payload = HotspotCardsPortalService(tenant_id=_tenant_id()).login(
            username=username,
            password=str(body.get("password") or ""),
        )
        return _json_result(payload)
    except HotspotCardsPortalError as exc:
        return _json_error(exc.code, exc.message, exc.status)


def me():
    try:
        return _json_result(HotspotCardsPortalService(tenant_id=_tenant_id()).me(_token()))
    except HotspotCardsPortalError as exc:
        return _json_error(exc.code, exc.message, exc.status)


def catalog():
    try:
        return _json_result(HotspotCardsPortalService(tenant_id=_tenant_id()).catalog(_token()))
    except HotspotCardsPortalError as exc:
        return _json_error(exc.code, exc.message, exc.status)


def my_cards():
    try:
        return _json_result(HotspotCardsPortalService(tenant_id=_tenant_id()).my_cards(_token()))
    except HotspotCardsPortalError as exc:
        return _json_error(exc.code, exc.message, exc.status)


def purchase():
    body = _body()
    try:
        return _json_result(
            HotspotCardsPortalService(tenant_id=_tenant_id()).purchase(
                token=_token(),
                catalog_item_id=body.get("catalog_item_id"),
                client_request_id=str(body.get("client_request_id") or ""),
            )
        )
    except HotspotCardsPortalError as exc:
        return _json_error(exc.code, exc.message, exc.status)


def send_sms():
    body = _body()
    try:
        return _json_result(
            HotspotCardsPortalService(tenant_id=_tenant_id()).send_sms(
                token=_token(),
                purchase_id=body.get("purchase_id"),
                phone=str(body.get("phone") or ""),
            )
        )
    except HotspotCardsPortalError as exc:
        return _json_error(exc.code, exc.message, exc.status)
