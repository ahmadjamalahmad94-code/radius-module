"""Network device registry API.

Mirrors the existing web registry for devices behind managed routers:
access points, switches, cameras, servers, and similar LAN devices.
This API is intentionally router-safe: it stores inventory rows and can
run the same manual TCP reachability check as the web page, but it does
not create bypass rules or open remote-access forwards.
"""
from __future__ import annotations

import socket
import time
from typing import Any

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


_TYPE_LABELS = {
    "ap": "نقطة وصول",
    "router": "راوتر",
    "switch": "سويتش",
    "camera": "كاميرا",
    "nvr": "مسجل كاميرات",
    "server": "خادم",
    "other": "جهاز آخر",
}

_STATUS_LABELS = {
    "up": "يستجيب",
    "down": "لا يستجيب",
    "unknown": "غير مفحوص",
}


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/network-devices",
        "network_devices_list",
        require_api_token(network_devices_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/network-devices",
        "network_devices_create",
        require_api_token(network_devices_create),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/network-devices/<int:device_id>",
        "network_devices_get",
        require_api_token(network_devices_get),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/network-devices/<int:device_id>",
        "network_devices_patch",
        require_api_token(network_devices_patch),
        methods=["PATCH"],
    )
    bp.add_url_rule(
        "/network-devices/<int:device_id>",
        "network_devices_delete",
        require_api_token(network_devices_delete),
        methods=["DELETE"],
    )
    bp.add_url_rule(
        "/network-devices/<int:device_id>/check",
        "network_devices_check",
        require_api_token(network_devices_check),
        methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _router_map(tenant_id: int) -> dict[int, dict]:
    from ...radius.db.repos import nas_repo

    routers = nas_repo.list_nas(tenant_id, limit=500)
    return {
        int(router.id): {
            "id": int(router.id),
            "name": router.name or "",
            "address": router.address or "",
        }
        for router in routers
        if router.id is not None
    }


def _router_exists(tenant_id: int, router_id: int) -> bool:
    from ...radius.db.repos import nas_repo

    return nas_repo.get_nas(tenant_id, router_id) is not None


def _serialize(row: dict, routers: dict[int, dict] | None = None) -> dict:
    routers = routers or {}
    router = routers.get(int(row.get("router_id") or 0), {})
    device_type = str(row.get("device_type") or "other")
    status = str(row.get("last_status") or "unknown")
    return {
        "id": int(row["id"]),
        "router_id": int(row.get("router_id") or 0),
        "router_name": router.get("name", ""),
        "router_address": router.get("address", ""),
        "name": row.get("name") or "",
        "device_type": device_type,
        "device_type_label": _TYPE_LABELS.get(device_type, "جهاز آخر"),
        "ip_address": row.get("ip_address") or "",
        "mac_address": row.get("mac_address") or "",
        "location": row.get("location") or "",
        "management_port": int(row.get("management_port") or 80),
        "notes": row.get("notes") or "",
        "is_critical": bool(row.get("is_critical")),
        "watch_enabled": bool(row.get("watch_enabled")),
        "alert_enabled": bool(row.get("alert_enabled")),
        "last_status": status,
        "last_status_label": _STATUS_LABELS.get(status, "غير مفحوص"),
        "last_checked_at": row.get("last_checked_at") or "",
        "last_latency_ms": row.get("last_latency_ms"),
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }


def _summary(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "up": sum(1 for item in items if item.get("last_status") == "up"),
        "down": sum(1 for item in items if item.get("last_status") == "down"),
        "unknown": sum(
            1 for item in items if item.get("last_status") == "unknown"
        ),
        "watched": sum(1 for item in items if item.get("watch_enabled")),
        "critical": sum(1 for item in items if item.get("is_critical")),
        "alerts": sum(1 for item in items if item.get("alert_enabled")),
    }


def _editable_fields(body: dict) -> dict:
    fields: dict[str, Any] = {}
    for key in (
        "name",
        "device_type",
        "ip_address",
        "mac_address",
        "location",
        "management_port",
        "notes",
        "is_critical",
        "watch_enabled",
        "alert_enabled",
    ):
        if key in body:
            fields[key] = body[key]
    return fields


def network_devices_list():
    from ...radius.db.repos import network_devices_repo

    tenant_id = _tid()
    router_id = _int_or_none(request.args.get("router_id"))
    status = (request.args.get("status") or "").strip().lower()
    device_type = (request.args.get("type") or "").strip().lower()
    query = (request.args.get("q") or "").strip().lower()

    items = network_devices_repo.list_for_tenant(tenant_id, router_id=router_id)
    if status:
        items = [item for item in items if item.get("last_status") == status]
    if device_type:
        items = [
            item for item in items if item.get("device_type") == device_type
        ]
    if query:
        items = [
            item
            for item in items
            if query
            in " ".join(
                str(item.get(key) or "").lower()
                for key in ("name", "ip_address", "mac_address", "location")
            )
        ]

    routers = _router_map(tenant_id)
    return ok(
        {
            "items": [_serialize(item, routers) for item in items],
            "count": len(items),
            "summary": _summary(items),
            "routers": list(routers.values()),
            "filters": {
                "router_id": router_id,
                "status": status,
                "type": device_type,
                "q": query,
            },
        }
    )


def network_devices_get(device_id: int):
    from ...radius.db.repos import network_devices_repo

    tenant_id = _tid()
    item = network_devices_repo.get_by_id(tenant_id, device_id)
    if not item:
        return fail("not_found", "جهاز الشبكة غير موجود.", status=404)
    return ok({"device": _serialize(item, _router_map(tenant_id))})


def network_devices_create():
    from ...radius.db.repos import network_devices_repo

    tenant_id = _tid()
    body = _body()
    router_id = _int_or_none(body.get("router_id"))
    name = str(body.get("name") or "").strip()
    if not router_id:
        return fail("validation_error", "اختر الراوتر التابع له الجهاز.", status=422)
    if not _router_exists(tenant_id, router_id):
        return fail("validation_error", "الراوتر المختار غير موجود.", status=422)
    if not name:
        return fail("validation_error", "اسم الجهاز مطلوب.", status=422)

    new_id = network_devices_repo.create(
        tenant_id=tenant_id,
        router_id=router_id,
        name=name,
        device_type=body.get("device_type", "other"),
        ip_address=body.get("ip_address", ""),
        mac_address=body.get("mac_address", ""),
        location=body.get("location", ""),
        management_port=body.get("management_port", 80),
        notes=body.get("notes", ""),
        is_critical=body.get("is_critical", False),
        watch_enabled=body.get("watch_enabled", False),
        alert_enabled=body.get("alert_enabled", False),
    )
    item = network_devices_repo.get_by_id(tenant_id, new_id)
    return ok({"device": _serialize(item, _router_map(tenant_id))}, status=201)


def network_devices_patch(device_id: int):
    from ...radius.db.connection import transaction
    from ...radius.db.helpers import now_iso
    from ...radius.db.repos import network_devices_repo

    tenant_id = _tid()
    item = network_devices_repo.get_by_id(tenant_id, device_id)
    if not item:
        return fail("not_found", "جهاز الشبكة غير موجود.", status=404)

    body = _body()
    if "router_id" in body:
        router_id = _int_or_none(body.get("router_id"))
        if not router_id:
            return fail("validation_error", "اختر الراوتر التابع له الجهاز.", status=422)
        if not _router_exists(tenant_id, router_id):
            return fail("validation_error", "الراوتر المختار غير موجود.", status=422)
        if router_id != item["router_id"]:
            with transaction() as conn:
                conn.execute(
                    "UPDATE network_devices SET router_id = ?, updated_at = ? "
                    "WHERE tenant_id = ? AND id = ?",
                    (router_id, now_iso(), tenant_id, device_id),
                )

    fields = _editable_fields(body)
    if "name" in fields and not str(fields["name"] or "").strip():
        return fail("validation_error", "اسم الجهاز مطلوب.", status=422)
    if fields:
        network_devices_repo.update(tenant_id, device_id, **fields)

    updated = network_devices_repo.get_by_id(tenant_id, device_id)
    return ok({"device": _serialize(updated, _router_map(tenant_id))})


def network_devices_delete(device_id: int):
    from ...radius.db.repos import network_devices_repo

    tenant_id = _tid()
    if not network_devices_repo.get_by_id(tenant_id, device_id):
        return fail("not_found", "جهاز الشبكة غير موجود.", status=404)
    deleted = network_devices_repo.delete(tenant_id, device_id)
    return ok({"deleted": device_id, "removed": bool(deleted)})


def network_devices_check(device_id: int):
    from ...radius.db.repos import network_devices_repo

    tenant_id = _tid()
    item = network_devices_repo.get_by_id(tenant_id, device_id)
    if not item:
        return fail("not_found", "جهاز الشبكة غير موجود.", status=404)
    ip = item.get("ip_address") or ""
    if not ip:
        return fail("validation_error", "عنوان IP للجهاز فارغ.", status=422)

    status, latency_ms = _tcp_probe(ip, int(item.get("management_port") or 80))
    network_devices_repo.set_last_check(
        tenant_id=tenant_id,
        device_id=device_id,
        status=status,
        latency_ms=latency_ms,
    )
    message = (
        "الجهاز يستجيب."
        if status == "up"
        else "تعذر الوصول إلى الجهاز من الخادم."
    )
    updated = network_devices_repo.get_by_id(tenant_id, device_id)
    return ok(
        {
            "status": status,
            "ok": status == "up",
            "latency_ms": latency_ms,
            "message": message,
            "device": _serialize(updated, _router_map(tenant_id)),
        }
    )


def _tcp_probe(host: str, port: int, timeout_sec: float = 2.0) -> tuple[str, float | None]:
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout_sec)
        return "up", round((time.perf_counter() - started) * 1000.0, 1)
    except (socket.timeout, OSError):
        return "down", None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
