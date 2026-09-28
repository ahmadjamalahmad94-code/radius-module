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

import json
import socket
from dataclasses import asdict, replace
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.constants import NAS_VENDORS
from ...radius.core.errors import (
    RadiusConflict,
    RadiusError,
    RadiusNotFound,
    RadiusValidationError,
)
from ...radius.core.strict_input import iso_utc_z, parse_ranged_int, parse_strict_bool
from ...radius.core.types import NasDevice
from ...radius.services.license_admin_capacity import (
    CapacityEnforcementService,
    capacity_error_response,
)
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

# Defaults applied when a port is sent as null/"" (was: silently 0).
_INT_DEFAULTS = {
    "ports": 0, "auth_port": 1812, "acct_port": 1813, "coa_port": 3799,
    "api_port": 8728, "ssh_port": 22,
}
_INT_LABELS = {
    "ports": "عدد المنافذ", "auth_port": "منفذ المصادقة",
    "acct_port": "منفذ المحاسبة", "coa_port": "منفذ CoA",
    "api_port": "منفذ API", "ssh_port": "منفذ SSH",
}
_BOOL_LABELS = {
    "api_use_tls": "API عبر TLS", "monitoring_enabled": "المراقبة",
    "enabled": "التفعيل",
    "require_message_authenticator": "Message-Authenticator",
}


def _coerce_int(name: str, v: Any) -> int:
    lo = 0 if name == "ports" else 1
    return parse_ranged_int(v, label=_INT_LABELS.get(name, name), minimum=lo,
                            maximum=65535, default=_INT_DEFAULTS.get(name, 0))


def _coerce_str(name: str, v: Any) -> str:
    if v is None:
        return ""
    if name == "tags" and isinstance(v, (list, tuple)):
        return ",".join(str(x).strip() for x in v if str(x).strip())
    if name == "metadata" and isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (dict, list, tuple)):
        raise RadiusValidationError(f"قيمة «{name}» يجب أن تكون نصًّا.")
    return str(v)


def _apply_body(device: NasDevice, body: dict) -> NasDevice:
    """Strict whitelist intake. Range/format rules live in NasDevicesService
    (shared with the web form); here we only reject wrong JSON types
    (``"false"`` is NOT truthy, ``true`` is not a port, 1812.9 is not 1812)."""
    changes: dict = {}
    for k in _STR_FIELDS:
        if k in body:
            changes[k] = _coerce_str(k, body[k])
    for k in _INT_FIELDS:
        if k in body:
            changes[k] = _coerce_int(k, body[k])
    for k in _BOOL_FIELDS:
        if k in body:
            changes[k] = parse_strict_bool(body[k], label=_BOOL_LABELS.get(k, k))
    if "vendor" in changes:
        changes["vendor"] = changes["vendor"].strip().lower() or "mikrotik"
        if changes["vendor"] not in _VALID_VENDORS:
            raise RadiusValidationError(
                f"نوع الجهاز غير معروف: «{changes['vendor'][:40]}». "
                f"المسموح: {'، '.join(sorted(_VALID_VENDORS))}."
            )
    if "nas_type" in changes:
        changes["nas_type"] = changes["nas_type"].strip().lower()
    return replace(device, **changes)


def _json_body():
    """The JSON object body, or None when the body is not an object."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    return body if isinstance(body, dict) else None


def _conflict(e: RadiusConflict):
    return fail("nas_address_conflict", e.message, status=409,
                details=e.details or None)


def _serialize(device: NasDevice, *, radius_client_warning: dict | None = None) -> dict:
    """Drop secret-bearing fields before responding."""
    d = asdict(device)
    for k in ("last_seen_at", "last_check_at", "created_at", "updated_at",
              "deleted_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = iso_utc_z(v)
    for s in _SECRET_FIELDS:
        d.pop(s, None)
    if radius_client_warning:
        d["radius_client"] = radius_client_warning
        d["warning"] = radius_client_warning.get("message")
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
        limit = min(max(int(request.args.get("limit") or 100), 1), 500)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "قيم limit و offset يجب أن تكون أرقامًا صحيحة.", status=422)
    items = _svc().list(limit=limit, offset=offset)
    return ok({"items": [_serialize(d) for d in items], "count": len(items)})


def nas_create():
    body = _json_body()
    if body is None:
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    name = body.get("name")
    address = body.get("address")
    if not isinstance(name, str) or not name.strip():
        return fail("validation_error", "اسم الراوتر مطلوب.", status=422)
    if not isinstance(address, str) or not address.strip():
        return fail("validation_error", "عنوان الراوتر مطلوب.", status=422)
    name, address = name.strip(), address.strip()
    capacity = CapacityEnforcementService().check_create(
        tenant_id=_tid(),
        feature_key="nas",
        limit_path="nas.max_total",
        usage_metric="nas_count",
    )
    if not capacity.allowed:
        return capacity_error_response(capacity)

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

    svc = _svc()
    try:
        saved = svc.create(actor=_actor(), device=device)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusConflict as e:
        return _conflict(e)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(saved, radius_client_warning=svc.radius_client_warning),
              status=201)


def nas_get(nas_id: int):
    try:
        device = _svc().get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"الراوتر {nas_id} غير موجود.", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(device))


def nas_patch(nas_id: int):
    body = _json_body()
    if body is None:
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    svc = _svc()
    try:
        existing = svc.get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"الراوتر {nas_id} غير موجود.", status=404)
    try:
        new_device = _apply_body(existing, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        svc.update(actor=_actor(), device=new_device)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusConflict as e:
        return _conflict(e)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(svc.get(nas_id),
                         radius_client_warning=svc.radius_client_warning))


def nas_delete(nas_id: int):
    # Adapter delete is silent on missing rows — check first for a clean 404.
    try:
        _svc().get(nas_id)
    except RadiusNotFound:
        return fail("not_found", f"الراوتر {nas_id} غير موجود.", status=404)
    try:
        _svc().delete(actor=_actor(), nas_id=nas_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": nas_id, "archived": True})


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
        return fail("not_found", f"الراوتر {nas_id} غير موجود.", status=404)

    ip = device.address
    try:
        port = int(device.api_port or 8728)
    except (TypeError, ValueError):
        port = 8728

    import time
    status = "unknown"
    message = ""
    start = time.monotonic()
    if not (1 <= port <= 65535):
        status = "unreachable"
        message = f"منفذ API غير صالح ({port}) — عدّل إعدادات الراوتر."
    else:
        from ...radius.services.devices import probe_nas_tcp
        status, message = probe_nas_tcp(ip, port)
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

