"""
NAS endpoints — full CUD + reachability test for the Flutter client.

All writes go through `NasDevicesService`, the same path the Flask web
admin form uses, so audit + adapter sync stay identical. Field intake is
a whitelist; `secret` is write-only and never returned in the response.

The /test endpoint mirrors the web `devices_test` action — TCP socket
reachability check against `api_port` with a 2s timeout, then records the
result via `nas_repo.record_check`.
"""
from __future__ import annotations

import socket
from dataclasses import asdict, replace
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.constants import NAS_VENDORS
from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.core.types import NasDevice
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


# Whitelist for create/patch. `id`, `tenant_id`, `last_*`, `created_at`,
# `updated_at` are not editable here. `secret` is included so callers can
# set/rotate it on write, but it's never returned by `_serialize`.
_STR_FIELDS = (
    "name", "address", "secret", "vendor", "nas_type", "shortname",
    "snmp_community", "api_user", "api_password",
    "location", "coordinates", "description",
    "tags", "metadata",
)
_INT_FIELDS = (
    "ports", "auth_port", "acct_port", "coa_port", "api_port", "ssh_port",
)
_BOOL_FIELDS = (
    "api_use_tls", "monitoring_enabled", "enabled",
    "require_message_authenticator",
)
_VALID_VENDORS = set(NAS_VENDORS)
_SECRET_FIELDS = {"secret", "api_password"}


def _coerce_int(name: str, v: Any) -> int:
    if v in (None, ""):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{name} must be integer")


def _apply_body(device: NasDevice, body: dict) -> NasDevice:
    changes: dict = {}
    for k in _STR_FIELDS:
        if k in body:
            v = body[k]
            changes[k] = "" if v is None else str(v)
    for k in _INT_FIELDS:
        if k in body:
            changes[k] = _coerce_int(k, body[k])
    for k in _BOOL_FIELDS:
        if k in body:
            changes[k] = bool(body[k])
    if "vendor" in changes and changes["vendor"]:
        changes["vendor"] = changes["vendor"].strip().lower()
        if changes["vendor"] not in _VALID_VENDORS:
            raise RadiusValidationError(
                f"unknown vendor: {changes['vendor']!r}. "
                f"Allowed: {sorted(_VALID_VENDORS)}"
            )
    if "nas_type" in changes:
        changes["nas_type"] = changes["nas_type"].strip().lower()
    return replace(device, **changes)


def _serialize(device: NasDevice) -> dict:
    """Drop secret-bearing fields before responding."""
    d = asdict(device)
    for k in ("last_seen_at", "last_check_at", "created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat() + "Z"
    for s in _SECRET_FIELDS:
        d.pop(s, None)
    return d


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/nas", "nas_list",
                    require_api_token(nas_list), methods=["GET"])
    bp.add_url_rule("/nas", "nas_create",
                    require_api_token(nas_create), methods=["POST"])
    bp.add_url_rule("/nas/<int:nas_id>", "nas_get",
                    require_api_token(nas_get), methods=["GET"])
    bp.add_url_rule("/nas/<int:nas_id>", "nas_patch",
                    require_api_token(nas_patch), methods=["PATCH"])
    bp.add_url_rule("/nas/<int:nas_id>", "nas_delete",
                    require_api_token(nas_delete), methods=["DELETE"])
    bp.add_url_rule("/nas/<int:nas_id>/test", "nas_test",
                    require_api_token(nas_test), methods=["POST"])


def _svc():
    from ...radius.services.devices import get_nas_devices_service
    return get_nas_devices_service()


# ─────────────── views ───────────────

def nas_list():
    try:
        limit = min(int(request.args.get("limit") or 100), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    items = _svc().list(limit=limit, offset=offset)
    return ok({"items": [_serialize(d) for d in items], "count": len(items)})


def nas_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip()
    if not name:
        return fail("validation_error", "name مطلوب", status=422)
    if not address:
        return fail("validation_error", "address مطلوب", status=422)

    seed = NasDevice(
        id=None,
        tenant_id=_tid(),
        name=name,
        address=address,
        secret="",
        vendor="mikrotik",
    )
    try:
        device = _apply_body(seed, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)

    try:
        saved = _svc().create(actor=_actor(), device=device)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(saved), status=201)


def nas_get(nas_id: int):
    try:
        device = _svc().get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"nas {nas_id} غير موجود", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(device))


def nas_patch(nas_id: int):
    body = request.get_json(silent=True) or {}
    try:
        existing = _svc().get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"nas {nas_id} غير موجود", status=404)
    try:
        new_device = _apply_body(existing, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        _svc().update(actor=_actor(), device=new_device)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(_svc().get(nas_id)))


def nas_delete(nas_id: int):
    # Adapter delete is silent on missing rows — check first for a clean 404.
    try:
        _svc().get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"nas {nas_id} غير موجود", status=404)
    try:
        _svc().delete(actor=_actor(), nas_id=nas_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": nas_id})


def nas_test(nas_id: int):
    """TCP reachability against api_port with 2s timeout. Records result via
    nas_repo.record_check so the dashboard / list sees the latest status.

    Returns:
      { ok: true, data: { status, ip, port, ms, message } }
      status ∈ {"reachable", "timeout", "unreachable"}
    """
    try:
        device = _svc().get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"nas {nas_id} غير موجود", status=404)

    ip = device.address
    try:
        port = int(device.api_port or 8728)
    except (TypeError, ValueError):
        port = 8728

    import time
    status = "unknown"
    message = ""
    start = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=2.0):
            status = "reachable"
            message = f"الاتصال نجح على {ip}:{port}"
    except socket.timeout:
        status = "timeout"
        message = f"انتهت المهلة (2s) على {ip}:{port}"
    except OSError as exc:
        status = "unreachable"
        message = f"تعذّر الاتصال: {exc}"
    ms = int((time.monotonic() - start) * 1000)

    try:
        from ...radius.db.repos import nas_repo
        nas_repo.record_check(_tid(), nas_id, status=status)
    except Exception:  # noqa: BLE001
        pass

    return ok({
        "status": status,
        "ip": ip,
        "port": port,
        "ms": ms,
        "message": message,
        "ok": status == "reachable",
    })
