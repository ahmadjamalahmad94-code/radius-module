"""device-health — v1 JSON API (feat/api-first-parity, group 7a).

Mirrors the MikroTik device-health page (`routes/device_health.py`,
`/admin/radius/device-health`) as token-authed `/api/v1` JSON. Reuses the
`device_health` service + `device_health_repo` (no duplicated logic): summary +
device list, CRUD, monitoring enable/disable, status events + alert history,
router-interface lookup, the panel live-apply toggle, and a single-device
reachability test-ping.

Deferred to the group-7 follow-up (heavy live-wire): bulk `poll` (+ stream) and
per-device `apply` — they push to the router and are gated by
`HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY`; the test-ping probe is included as the
light reachability check.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import device_health_repo as repo
from ...radius.services import device_health as svc
from ...radius.services.device_health import DeviceHealthError
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def register(bp: Blueprint) -> None:
    R = "/device-health"
    bp.add_url_rule(R, "device_health_overview", require_api_token(overview), methods=["GET"])
    bp.add_url_rule(f"{R}/devices", "device_health_list", require_api_token(list_devices), methods=["GET"])
    bp.add_url_rule(f"{R}/devices", "device_health_create", require_api_token(create_device), methods=["POST"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>", "device_health_update",
                    require_api_token(update_device), methods=["PATCH"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>", "device_health_delete",
                    require_api_token(delete_device), methods=["DELETE"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>/enable", "device_health_enable",
                    require_api_token(enable_device), methods=["POST"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>/disable", "device_health_disable",
                    require_api_token(disable_device), methods=["POST"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>/events", "device_health_events",
                    require_api_token(device_events), methods=["GET"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>/alerts", "device_health_alerts",
                    require_api_token(device_alerts), methods=["GET"])
    bp.add_url_rule(f"{R}/devices/<int:device_id>/test-ping", "device_health_test_ping",
                    require_api_token(test_ping), methods=["POST"])
    bp.add_url_rule(f"{R}/router-interfaces", "device_health_router_interfaces",
                    require_api_token(router_interfaces), methods=["GET"])
    bp.add_url_rule(f"{R}/live-apply", "device_health_live_apply_get",
                    require_api_token(live_apply_get), methods=["GET"])
    bp.add_url_rule(f"{R}/live-apply", "device_health_live_apply_set",
                    require_api_token(live_apply_set), methods=["POST"])


def _filters() -> dict:
    a = request.args
    f: dict = {}
    if a.get("router_id"):
        f["router_id"] = _int(a.get("router_id"))
    if a.get("status"):
        f["status"] = a.get("status")
    if a.get("device_type"):
        f["device_type"] = a.get("device_type")
    return f


def overview():
    """GET /device-health — ملخّص + قائمة الأجهزة + الراوترات للقائمة المنسدلة."""
    tid = _tid()
    data = svc.list_with_routers(tid, **_filters())
    return ok({
        "summary": svc.summary(tid),
        "devices": data["devices"],
        "routers": svc.routers_for_dropdown(tid),
    })


def list_devices():
    """GET /device-health/devices — قائمة الأجهزة + الملخّص (يطابق api_list)."""
    tid = _tid()
    data = svc.list_with_routers(tid, **_filters())
    return ok({"devices": data["devices"], "summary": svc.summary(tid)})


def create_device():
    """POST /device-health/devices — إنشاء جهاز مراقَب (يطابق api_create)."""
    tid = _tid()
    try:
        result = svc.create_device(tid, _body())
    except DeviceHealthError as exc:
        return fail("validation_error", str(exc), status=422)
    return ok({
        "device": repo.get_device(tid, result["device_id"]),
        "warnings": result.get("warnings", []),
        "network": result.get("network"),
    }, status=201)


def update_device(device_id: int):
    """PATCH /device-health/devices/<id> — تعديل (يطابق api_update)."""
    tid = _tid()
    try:
        result = svc.update_device(tid, device_id, _body())
    except DeviceHealthError as exc:
        return fail("validation_error", str(exc), status=422)
    return ok({"device": repo.get_device(tid, device_id),
               "warnings": result.get("warnings", [])})


def delete_device(device_id: int):
    """DELETE /device-health/devices/<id> — حذف (يطابق api_delete)."""
    actor = getattr(g, "admin_username", None) or "api"
    deleted = svc.delete_device(_tid(), device_id, actor=actor)
    return ok({"id": device_id, "deleted": bool(deleted)})


def enable_device(device_id: int):
    svc.set_monitoring(_tid(), device_id, True)
    return ok({"id": device_id, "monitoring_enabled": True})


def disable_device(device_id: int):
    svc.set_monitoring(_tid(), device_id, False)
    return ok({"id": device_id, "monitoring_enabled": False})


def device_events(device_id: int):
    """GET /device-health/devices/<id>/events — سجلّ تغيّر الحالة."""
    return ok({"events": repo.list_events(_tid(), device_id=device_id, limit=100)})


def device_alerts(device_id: int):
    """GET /device-health/devices/<id>/alerts — قرارات التنبيه."""
    return ok({"alerts": repo.list_alerts(_tid(), device_id=device_id)})


def test_ping(device_id: int):
    """POST /device-health/devices/<id>/test-ping — فحص وصول حيّ (يطابق api_test_ping)."""
    try:
        result = svc.test_ping(_tid(), device_id)
    except DeviceHealthError as exc:
        return fail("validation_error", str(exc), status=422)
    return ok(result)


def router_interfaces():
    """GET /device-health/router-interfaces?router_id= — واجهات الراوتر."""
    router_id = _int(request.args.get("router_id"))
    if not router_id:
        return ok({"online": False, "interfaces": []})
    try:
        return ok(svc.list_router_interfaces(_tid(), router_id))
    except DeviceHealthError as exc:
        return fail("not_found", str(exc), status=404)


def live_apply_get():
    """GET /device-health/live-apply — حالة مفتاح التطبيق الحيّ."""
    return ok(svc.live_apply_state(_tid()))


def live_apply_set():
    """POST /device-health/live-apply {enabled} — ضبط مفتاح التطبيق الحيّ."""
    enabled = str(_body().get("enabled")).strip().lower() in {"1", "true", "yes", "on"}
    by = _int(getattr(g, "admin_id", 0))
    return ok(svc.set_live_apply(_tid(), enabled, by=by))
