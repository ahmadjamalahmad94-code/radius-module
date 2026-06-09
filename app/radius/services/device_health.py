"""device_health — business logic for «تتبع حالة الأجهزة».

Sits between the routes and the repo/planner. Responsibilities:
  • summary()           — KPI counts for the header cards.
  • list_with_routers() — devices enriched with their router name.
  • create_device()     — validate (interface mandatory, IP valid, no
                          duplicate), compute network/gateway, persist the
                          device + its network-scope and binding rows
                          (apply_status=pending — NO live MikroTik mutation).
  • update_device()     — patch + recompute network when IP/prefix change.
  • intended_plan()     — Phase 1 dry-run plan (no router I/O).
  • live_plan()         — Phase 2 read-only diff against the router.

This module performs NO MikroTik writes. Phase 3 will add a controlled apply
path that reuses device_health_mikrotik's gated write helpers.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.tenant import DEFAULT_TENANT_ID  # noqa: F401  (kept for parity)
from ..db.repos import device_health_repo as repo
from ..db.repos import nas_repo
from . import device_health_planner as planner

# device_health_mikrotik is imported lazily inside live_plan()/test_ping() so
# the Phase-1 surface (DB + CRUD + dry-run plan) carries no dependency on the
# Phase-2 router wrapper.


class DeviceHealthError(ValueError):
    """User-facing validation error (Arabic message)."""


# Status → KPI bucket. timeout counts as down; high_latency its own card.
def summary(tenant_id: int) -> dict:
    devices = repo.list_devices(int(tenant_id))
    total = len(devices)
    up = sum(1 for d in devices if d["status"] == "up")
    down = sum(1 for d in devices if d["status"] in ("down", "timeout"))
    high = sum(1 for d in devices if d["status"] == "high_latency")
    disabled = sum(1 for d in devices if not d["monitoring_enabled"])
    return {
        "total": total, "up": up, "down": down,
        "high_latency": high, "disabled": disabled,
    }


def routers_for_dropdown(tenant_id: int) -> list[dict]:
    rows = nas_repo.list_nas(int(tenant_id), limit=500)
    return [{"id": n.id, "name": n.name, "address": n.address} for n in rows]


def list_with_routers(tenant_id: int, **filters: Any) -> dict:
    """Devices (filtered) + a {router_id: name} map for the table."""
    devices = repo.list_devices(int(tenant_id), **filters)
    routers = routers_for_dropdown(int(tenant_id))
    router_names = {r["id"]: r["name"] for r in routers}
    for d in devices:
        d["router_name"] = router_names.get(d["router_id"], "—")
    return {"devices": devices, "routers": routers,
            "router_names": router_names}


def _require_router(tenant_id: int, router_id: int):
    nas = nas_repo.get_nas(int(tenant_id), int(router_id))
    if not nas:
        raise DeviceHealthError("الراوتر المُختار غير موجود.")
    return nas


def create_device(tenant_id: int, params: dict) -> dict:
    """Validate + persist a new device. Returns {device_id, network, warnings}.
    Raises DeviceHealthError on any validation failure. NO router mutation."""
    tid = int(tenant_id)
    router_id = _to_int(params.get("router_id"), 0)
    if not router_id:
        raise DeviceHealthError("اختر الراوتر الذي يتبع له الجهاز.")
    _require_router(tid, router_id)

    name = str(params.get("name") or "").strip()
    if not name:
        raise DeviceHealthError("اسم الجهاز مطلوب.")

    interface_name = str(params.get("interface_name") or "").strip()
    if not interface_name:
        raise DeviceHealthError("المدخل (interface) إلزامي.")

    ip_address = str(params.get("ip_address") or "").strip()
    if not ip_address:
        raise DeviceHealthError("عنوان IP للجهاز مطلوب.")

    subnet_prefix = _to_int(params.get("subnet_prefix"), 24)
    gateway_last_octet = _to_int(params.get("gateway_last_octet"), 254)

    # Network calc — raises a friendly error for bad IP/prefix/octet.
    try:
        net = planner.compute_network(ip_address, subnet_prefix, gateway_last_octet)
    except planner.NetworkCalcError as exc:
        raise DeviceHealthError(str(exc)) from exc

    # Duplicate prevention: same router + same IP among the living.
    dup = repo.find_device_by_router_ip(tid, router_id, ip_address)
    if dup:
        raise DeviceHealthError(
            "هذا الجهاز موجود مسبقًا على نفس الراوتر بنفس الـIP "
            f"«{dup['name']}» (رقم {dup['id']})."
        )

    monitoring_enabled = _to_bool(params.get("monitoring_enabled"), True)
    device_id = repo.create_device(
        tenant_id=tid,
        router_id=router_id,
        name=name,
        interface_name=interface_name,
        ip_address=net["ip_address"],
        network_cidr=net["network_cidr"],
        gateway_address=net["gateway_address"],
        device_type=str(params.get("device_type") or "other"),
        location=str(params.get("location") or "").strip(),
        subnet_prefix=net["prefix"],
        gateway_last_octet=gateway_last_octet,
        ping_threshold_ms=_to_int(params.get("ping_threshold_ms"), 80),
        netwatch_interval_sec=_to_int(params.get("netwatch_interval_sec"), 60),
        netwatch_timeout_sec=_to_int(params.get("netwatch_timeout_sec"), 3),
        alert_channel=str(params.get("alert_channel") or ""),
        monitoring_enabled=monitoring_enabled,
        notes=str(params.get("notes") or "").strip(),
    )

    # Record the intended network scope + binding (apply_status=pending).
    # Detect the «same subnet on another interface» ambiguity locally too.
    warnings: list[str] = []
    existing_scopes = repo.scopes_for_network(tid, router_id, net["network_cidr"])
    other_ifaces = {s["interface_name"] for s in existing_scopes
                    if s["interface_name"] and s["interface_name"] != interface_name}
    if other_ifaces:
        warnings.append(
            "نفس الشبكة %s مسجّلة على مدخل آخر (%s) — غموض توجيه محتمل."
            % (net["network_cidr"], "، ".join(sorted(other_ifaces))))

    repo.upsert_scope(
        tenant_id=tid, router_id=router_id,
        interface_name=interface_name, network_cidr=net["network_cidr"],
        gateway_address=net["gateway_address"], apply_status="pending",
    )
    repo.upsert_binding(
        tenant_id=tid, router_id=router_id,
        network_cidr=net["network_cidr"], binding_type="bypassed",
        apply_status="pending",
    )
    repo.add_event(
        tenant_id=tid, device_id=device_id, event_type="created",
        new_status="unknown",
        message=f"تسجيل الجهاز «{name}» على {interface_name} ({ip_address}).",
    )
    return {"device_id": device_id, "network": net, "warnings": warnings}


def update_device(tenant_id: int, device_id: int, params: dict) -> dict:
    """Patch editable fields; recompute network when IP/prefix/octet change.
    Returns {ok, warnings}. Raises DeviceHealthError on validation failure."""
    tid = int(tenant_id)
    device = repo.get_device(tid, int(device_id))
    if not device:
        raise DeviceHealthError("الجهاز غير موجود.")

    fields: dict[str, Any] = {}
    for key in ("name", "device_type", "location", "alert_channel",
                "notes", "ping_threshold_ms", "netwatch_interval_sec",
                "netwatch_timeout_sec"):
        if key in params and params[key] is not None:
            fields[key] = params[key]
    if "monitoring_enabled" in params:
        fields["monitoring_enabled"] = _to_bool(params.get("monitoring_enabled"),
                                                device["monitoring_enabled"])

    # Router move — validate the target exists.
    if "router_id" in params and params["router_id"] not in (None, ""):
        new_router = _to_int(params["router_id"], device["router_id"])
        if new_router != device["router_id"]:
            _require_router(tid, new_router)
            fields["router_id"] = new_router

    # Network-affecting fields.
    ip_address = str(params.get("ip_address") or device["ip_address"]).strip()
    interface_name = str(params.get("interface_name")
                         or device["interface_name"]).strip()
    subnet_prefix = _to_int(params.get("subnet_prefix"), device["subnet_prefix"])
    gateway_last_octet = _to_int(params.get("gateway_last_octet"),
                                 device["gateway_last_octet"])
    net_changed = (
        ip_address != device["ip_address"]
        or interface_name != device["interface_name"]
        or subnet_prefix != device["subnet_prefix"]
        or gateway_last_octet != device["gateway_last_octet"]
    )
    warnings: list[str] = []
    if "interface_name" in params and not interface_name:
        raise DeviceHealthError("المدخل (interface) إلزامي.")
    if net_changed:
        try:
            net = planner.compute_network(ip_address, subnet_prefix, gateway_last_octet)
        except planner.NetworkCalcError as exc:
            raise DeviceHealthError(str(exc)) from exc
        # Duplicate check only if IP/router actually changed to a colliding pair.
        target_router = fields.get("router_id", device["router_id"])
        dup = repo.find_device_by_router_ip(tid, target_router, net["ip_address"])
        if dup and dup["id"] != device["id"]:
            raise DeviceHealthError(
                "جهاز آخر على نفس الراوتر يستخدم هذا الـIP "
                f"«{dup['name']}» (رقم {dup['id']}).")
        fields.update({
            "ip_address": net["ip_address"],
            "interface_name": interface_name,
            "network_cidr": net["network_cidr"],
            "gateway_address": net["gateway_address"],
            "subnet_prefix": net["prefix"],
            "gateway_last_octet": gateway_last_octet,
        })
        repo.upsert_scope(
            tenant_id=tid, router_id=target_router,
            interface_name=interface_name, network_cidr=net["network_cidr"],
            gateway_address=net["gateway_address"], apply_status="pending",
        )
        repo.upsert_binding(
            tenant_id=tid, router_id=target_router,
            network_cidr=net["network_cidr"], binding_type="bypassed",
            apply_status="pending",
        )

    changed = repo.update_device(tid, int(device_id), **fields)
    return {"ok": changed, "warnings": warnings}


def set_monitoring(tenant_id: int, device_id: int, enabled: bool) -> bool:
    ok = repo.set_monitoring(int(tenant_id), int(device_id), enabled)
    if ok:
        repo.add_event(
            tenant_id=int(tenant_id), device_id=int(device_id),
            event_type="updated",
            new_status=("unknown" if enabled else "disabled"),
            message=("تم تفعيل المراقبة." if enabled else "تم إيقاف المراقبة."),
        )
    return ok


def delete_device(tenant_id: int, device_id: int, *, actor: str = "") -> bool:
    return repo.soft_delete_device(int(tenant_id), int(device_id), actor=actor)


# ── plans ──────────────────────────────────────────────────────

def intended_plan(params: dict) -> dict:
    """Phase 1 dry-run plan — computed only, no router I/O."""
    return planner.build_plan(
        interface_name=str(params.get("interface_name")
                           or params.get("interface") or ""),
        ip_address=str(params.get("ip_address") or params.get("ip") or ""),
        subnet_prefix=_to_int(params.get("subnet_prefix"), 24),
        gateway_last_octet=_to_int(params.get("gateway_last_octet"), 254),
        netwatch_interval_sec=_to_int(params.get("netwatch_interval_sec"), 60),
        netwatch_timeout_sec=_to_int(params.get("netwatch_timeout_sec"), 3),
        router_state=None,
    )


def live_plan(tenant_id: int, device_id: int) -> dict:
    """Phase 2 read-only diff against the device's router. Reads
    /ip/address, /ip/hotspot/ip-binding, /tool/netwatch — NO mutation.
    Returns {ok, plan, router_state_ok, errors}."""
    tid = int(tenant_id)
    device = repo.get_device(tid, int(device_id))
    if not device:
        raise DeviceHealthError("الجهاز غير موجود.")
    nas = nas_repo.get_nas(tid, device["router_id"])
    if not nas:
        raise DeviceHealthError("الراوتر المرتبط بالجهاز غير موجود.")
    from . import device_health_mikrotik as mt
    nas_dict = _nas_to_dict(nas)
    state = mt.read_router_state(nas_dict)
    plan = planner.build_plan(
        interface_name=device["interface_name"],
        ip_address=device["ip_address"],
        subnet_prefix=device["subnet_prefix"],
        gateway_last_octet=device["gateway_last_octet"],
        netwatch_interval_sec=device["netwatch_interval_sec"],
        netwatch_timeout_sec=device["netwatch_timeout_sec"],
        router_state=state,
        device_id=device["id"],
    )
    return {"ok": True, "plan": plan,
            "router_state_ok": state.get("ok", False),
            "errors": state.get("errors", {})}


def test_ping(tenant_id: int, device_id: int) -> dict:
    """Diagnostic ping from the router to the device. Read-only.
    Returns {ok, status, latency_ms, error}. Persists the observed status."""
    tid = int(tenant_id)
    device = repo.get_device(tid, int(device_id))
    if not device:
        raise DeviceHealthError("الجهاز غير موجود.")
    if not device["ip_address"]:
        raise DeviceHealthError("لا يمكن الفحص — IP الجهاز فارغ.")
    nas = nas_repo.get_nas(tid, device["router_id"])
    if not nas:
        raise DeviceHealthError("الراوتر المرتبط بالجهاز غير موجود.")
    from . import device_health_mikrotik as mt
    res = mt.ping(_nas_to_dict(nas), target=device["ip_address"], count=4)
    if not res.ok:
        return {"ok": False, "status": "", "latency_ms": None, "error": res.error}

    status, latency = _summarize_ping(res.data or [], device["ping_threshold_ms"])
    repo.set_status(tenant_id=tid, device_id=int(device_id),
                    status=status, latency_ms=latency)
    repo.add_event(
        tenant_id=tid, device_id=int(device_id), event_type=status,
        previous_status=device["status"], new_status=status,
        latency_ms=latency, message="فحص ping يدوي.")
    return {"ok": True, "status": status, "latency_ms": latency, "error": ""}


def _summarize_ping(rows: list, threshold_ms: int) -> tuple[str, Optional[float]]:
    """Reduce /ping !re rows to (status, avg_latency_ms)."""
    latencies: list[float] = []
    received = 0
    for r in rows:
        # RouterOS returns time like '2ms' / '2us' / '1m500us'; also 'status'.
        t = str(r.get("time") or "").strip()
        if r.get("status") and not t:
            continue  # 'timeout' / 'host unreachable' rows
        ms = _parse_router_time_ms(t)
        if ms is not None:
            latencies.append(ms)
            received += 1
    if received == 0:
        return "down", None
    avg = sum(latencies) / len(latencies)
    if avg > float(threshold_ms or 80):
        return "high_latency", round(avg, 1)
    return "up", round(avg, 1)


def _parse_router_time_ms(text: str) -> Optional[float]:
    """Parse RouterOS ping time tokens ('2ms', '500us', '1s200ms') → ms."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    import re
    total = 0.0
    found = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)(us|ms|s)", s):
        found = True
        v = float(value)
        total += v / 1000.0 if unit == "us" else (v * 1000.0 if unit == "s" else v)
    if found:
        return round(total, 3)
    try:
        return float(s)  # bare number → assume ms
    except ValueError:
        return None


# ── helpers ────────────────────────────────────────────────────

def _nas_to_dict(nas) -> dict:
    return {
        "id":              nas.id,
        "tenant_id":       nas.tenant_id,
        "name":            nas.name,
        "address":         nas.address,
        "api_port":        nas.api_port,
        "api_user":        nas.api_user,
        "api_password":    nas.api_password,
        "api_use_tls":     nas.api_use_tls,
        "api_timeout_sec": getattr(nas, "api_timeout_sec", 3) or 3,
    }


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
