from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


_WINDOWS = {"day", "month"}


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/router-alerts/settings",
        "router_alerts_settings_get",
        require_api_token(router_alerts_settings_get),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/router-alerts/settings",
        "router_alerts_settings_patch",
        require_api_token(router_alerts_settings_patch),
        methods=["PATCH", "PUT"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _admin_id() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _positive_int(value: Any, *, field: str, minimum: int = 1) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(field) from exc
    if number < minimum:
        raise ValueError(field)
    return number


def _optional_positive_int(value: Any, *, field: str, minimum: int = 1) -> int | None:
    if value in (None, ""):
        return None
    return _positive_int(value, field=field, minimum=minimum)


def _usage_window(value: Any, default: str = "day") -> str:
    raw = str(value or default).strip().lower()
    if raw not in _WINDOWS:
        raise ValueError("usage_window")
    return raw


def _settings_payload(tenant_id: int) -> dict:
    from ...radius.db.repos import (
        nas_repo,
        router_alert_settings_repo,
        router_loop_probes_repo,
        router_metrics_repo,
    )
    from ...radius.services import smart_alerts

    glob = smart_alerts.global_settings(tenant_id)
    overrides = router_alert_settings_repo.list_for_tenant(tenant_id)
    last_push = router_metrics_repo.last_push_map(tenant_id)
    router_names: dict[int, str] = {}
    routers = []
    for router in nas_repo.list_nas(tenant_id, limit=1000):
        router_id = int(router.id or 0)
        router_names[router_id] = router.name or f"#{router_id}"
        effective = smart_alerts.effective_for_router(router_id, glob, overrides)
        override = overrides.get(router_id) or {}
        routers.append(
            {
                "id": router_id,
                "name": router_names[router_id],
                "address": router.address or "",
                "enabled": bool(effective["enabled"]),
                "offline_after_min": int(effective["offline_after_min"] or 0),
                "normal_speed_mbps": int(effective["normal_speed_mbps"] or 0),
                "normal_usage_gb": int(effective["normal_usage_gb"] or 0),
                "usage_window": effective["usage_window"] or "day",
                "last_push_at": last_push.get(router_id, ""),
                "has_override": bool(override),
            }
        )
    loop_probes = []
    for probe in router_loop_probes_repo.list_for_tenant(tenant_id):
        status = str(probe.get("last_status") or "").strip().lower()
        lease_ip = str(probe.get("last_lease_ip") or "").strip()
        loop_detected = status == "bound" or bool(lease_ip)
        router_id = int(probe.get("router_id") or 0)
        loop_probes.append(
            {
                "router_id": router_id,
                "router_name": router_names.get(router_id, f"#{router_id}"),
                "interface": str(probe.get("interface") or ""),
                "enabled": bool(int(probe.get("enabled") or 0)),
                "status": status,
                "lease_ip": lease_ip,
                "server_ip": str(probe.get("last_server_ip") or ""),
                "last_reading_at": str(probe.get("last_reading_at") or ""),
                "loop_detected": loop_detected,
            }
        )
    return {
        "settings": glob,
        "routers": routers,
        "loop_probes": loop_probes,
        "counts": {
            "routers": len(routers),
            "pushing": sum(1 for item in routers if item.get("last_push_at")),
            "overrides": sum(1 for item in routers if item.get("has_override")),
            "loop_probes": len(loop_probes),
            "loop_detected": sum(1 for item in loop_probes if item["loop_detected"]),
            "loop_routers": len({int(item["router_id"]) for item in loop_probes}),
        },
        "usage_windows": [
            {"key": "day", "label": "يومي"},
            {"key": "month", "label": "شهري"},
        ],
    }


def router_alerts_settings_get():
    tenant_id = _tid()
    try:
        from ...radius.services import smart_alerts

        smart_alerts.sweep_offline(tenant_id)
        smart_alerts.evaluate_all(tenant_id)
    except Exception:  # noqa: BLE001
        pass
    return ok(_settings_payload(tenant_id))


def router_alerts_settings_patch():
    from ...radius.db.repos import audit_repo, nas_repo, router_alert_settings_repo
    from ...radius.services import smart_alerts

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("validation_error", "بيانات الإعدادات مطلوبة.", status=422)

    tenant_id = _tid()
    errors: dict[str, str] = {}
    global_in = body.get("settings")
    routers_in = body.get("routers")

    try:
        if global_in is not None:
            if not isinstance(global_in, dict):
                raise ValueError("settings")
            values = {
                "enabled": _bool(global_in.get("enabled"), True),
                "telegram": _bool(global_in.get("telegram"), True),
                "offline": _bool(global_in.get("offline"), True),
                "high_traffic": _bool(global_in.get("high_traffic"), True),
                "high_usage": _bool(global_in.get("high_usage"), True),
                "loop": _bool(global_in.get("loop"), True),
                "offline_after_min": _positive_int(
                    global_in.get("offline_after_min", 6),
                    field="offline_after_min",
                    minimum=2,
                ),
                "default_speed_mbps": _positive_int(
                    global_in.get("default_speed_mbps", 100),
                    field="default_speed_mbps",
                ),
                "default_usage_gb": _positive_int(
                    global_in.get("default_usage_gb", 200),
                    field="default_usage_gb",
                ),
                "usage_window": _usage_window(global_in.get("usage_window", "day")),
            }
            smart_alerts.save_global_settings(tenant_id, values, by=_admin_id())

        if routers_in is not None:
            if not isinstance(routers_in, list):
                raise ValueError("routers")
            for item in routers_in:
                if not isinstance(item, dict):
                    raise ValueError("routers")
                router_id = _positive_int(item.get("id"), field="router_id")
                if nas_repo.get_nas(tenant_id, router_id) is None:
                    return fail(
                        "not_found",
                        "الراوتر غير موجود.",
                        status=404,
                        details={"router_id": router_id},
                    )
                router_alert_settings_repo.upsert(
                    tenant_id=tenant_id,
                    router_id=router_id,
                    enabled=_bool(item.get("enabled"), True),
                    offline_after_min=_optional_positive_int(
                        item.get("offline_after_min"),
                        field="offline_after_min",
                        minimum=2,
                    ),
                    normal_speed_mbps=_optional_positive_int(
                        item.get("normal_speed_mbps"),
                        field="normal_speed_mbps",
                    ),
                    normal_usage_gb=_optional_positive_int(
                        item.get("normal_usage_gb"),
                        field="normal_usage_gb",
                    ),
                    usage_window=(
                        _usage_window(item.get("usage_window"))
                        if item.get("usage_window") not in (None, "")
                        else None
                    ),
                )
    except ValueError as exc:
        field = str(exc) or "settings"
        errors[field] = "القيمة غير صالحة."
        return fail("validation_error", "راجع قيم إعدادات التنبيه.", status=422, details=errors)

    audit_repo.record(
        tenant_id=tenant_id,
        actor=f"api-token:{getattr(g, 'api_token_id', 'env')}",
        action="router_alerts_settings_update",
        target_type="router_alerts",
        target_id=str(tenant_id),
        payload={
            "settings": bool(global_in is not None),
            "routers": len(routers_in) if isinstance(routers_in, list) else 0,
        },
    )
    return ok(_settings_payload(tenant_id))
