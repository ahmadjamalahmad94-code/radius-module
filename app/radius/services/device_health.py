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

    # Duplicate prevention (1): same MikroTik/server + same device IP.
    dup = repo.find_device_by_router_ip(tid, router_id, ip_address)
    if dup:
        raise DeviceHealthError(
            "هذا الجهاز موجود مسبقًا على نفس المايكروتيك / السيرفر بنفس الـIP "
            f"«{dup['name']}» (رقم {dup['id']})."
        )

    # Duplicate prevention (2): same network range on the SAME (router +
    # interface) → HARD BLOCK (true duplicate scope). The same range on a
    # DIFFERENT interface is allowed below (separate scope + amber warning).
    scope_dup = repo.find_device_by_scope(
        tid, router_id, interface_name, net["network_cidr"])
    if scope_dup:
        raise DeviceHealthError(
            "هذا الرينج (%s) مُضاف على نفس المدخل (%s) مسبقاً عبر «%s» (رقم %s)."
            % (net["network_cidr"], interface_name,
               scope_dup["name"], scope_dup["id"]))

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
                "جهاز آخر على نفس المايكروتيك / السيرفر يستخدم هذا الـIP "
                f"«{dup['name']}» (رقم {dup['id']}).")
        # Same range on the SAME (router + interface) as ANOTHER device → block.
        scope_dup = repo.find_device_by_scope(
            tid, target_router, interface_name, net["network_cidr"],
            exclude_id=device["id"])
        if scope_dup:
            raise DeviceHealthError(
                "هذا الرينج (%s) مُضاف على نفس المدخل (%s) مسبقاً عبر «%s» (رقم %s)."
                % (net["network_cidr"], interface_name,
                   scope_dup["name"], scope_dup["id"]))
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


def list_router_interfaces(tenant_id: int, router_id: int) -> dict:
    """Live LAN-interface list for a router/CHR, for the add-device dropdown.

    Reads /interface via the existing MikroTik admin client, then reuses the
    loop/bt service's WAN+tunnel exclusion (port_script_services.filter_lan_ports
    with the router's configured WAN resolved like the loop service does). On an
    offline/unreachable router returns {online: False, interfaces: []} so the
    form can fall back to free-text — never blocks.
    """
    tid = int(tenant_id)
    nas = nas_repo.get_nas(tid, int(router_id))
    if not nas:
        raise DeviceHealthError("المايكروتيك / السيرفر غير موجود.")
    from . import port_script_services as pss
    from . import mikrotik_admin_client as mac
    rows = pss.discover_interfaces(_nas_to_dict(nas), mac.interface_list)
    if not rows:
        return {"online": False, "interfaces": []}
    wan = _resolve_wan_iface(tid, int(router_id))
    lan = pss.filter_lan_ports(rows, wan_iface=wan)
    seen: set[str] = set()
    names: list[str] = []
    for r in lan:
        n = str(r.get("name") or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return {"online": True, "interfaces": names}


def _resolve_wan_iface(tenant_id: int, router_id: int) -> str:
    """Same lookup the loop/bt service uses — the WAN saved by the setup
    wizard (setup_wizard_runs.selected_wan_interface). Empty → filter falls
    back to its default ether1 guard. Best-effort; never raises."""
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT selected_wan_interface FROM setup_wizard_runs "
            "WHERE tenant_id = ? AND router_id = ? "
            "  AND selected_wan_interface != '' "
            "ORDER BY id DESC LIMIT 1",
            (int(tenant_id), int(router_id)),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    return str(dict(row).get("selected_wan_interface") or "").strip() if row else ""


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

    status, latency = planner.summarize_ping(res.data or [], device["ping_threshold_ms"])
    repo.set_status(tenant_id=tid, device_id=int(device_id),
                    status=status, latency_ms=latency)
    repo.add_event(
        tenant_id=tid, device_id=int(device_id), event_type=status,
        previous_status=device["status"], new_status=status,
        latency_ms=latency, message="فحص ping يدوي.")
    return {"ok": True, "status": status, "latency_ms": latency, "error": ""}


# ── Phase 3: controlled live apply ─────────────────────────────

# MikroTik item-kind → (write fn name, repo bookkeeping). Only items the live
# plan marks `create` are written; `already_present` is recorded idempotently;
# nothing is ever removed.
_APPLY_KINDS = ("ip_address", "ip_binding", "netwatch")


def apply_device(tenant_id: int, device_id: int,
                 actions: Optional[list] = None) -> dict:
    """Phase 3 — apply ONLY the missing planned items to the router.

    Gated by device_health_mikrotik.live_apply_enabled() (env master switch):
    when off, returns gated=True and performs NO router I/O. Idempotent
    (`already_present` items are skipped), never destructive, writes managed-by
    comments, and records an audit-log entry per item.

    `actions` optionally restricts which kinds to apply (subset of
    ip_address/ip_binding/netwatch); default = all.
    """
    tid = int(tenant_id)
    from . import device_health_mikrotik as mt

    if not mt.live_apply_enabled():
        return {"ok": False, "gated": True,
                "error": ("التطبيق الحيّ على الراوتر معطّل — اضبط متغيّر البيئة "
                          "HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY=1 لتفعيله."),
                "applied": [], "already_present": [], "failed": []}

    device = repo.get_device(tid, int(device_id))
    if not device:
        raise DeviceHealthError("الجهاز غير موجود.")
    nas = nas_repo.get_nas(tid, device["router_id"])
    if not nas:
        raise DeviceHealthError("الراوتر المرتبط بالجهاز غير موجود.")

    wanted = set(actions) if actions else set(_APPLY_KINDS)
    nas_dict = _nas_to_dict(nas)
    state = mt.read_router_state(nas_dict)
    plan = planner.build_plan(
        interface_name=device["interface_name"],
        ip_address=device["ip_address"],
        subnet_prefix=device["subnet_prefix"],
        gateway_last_octet=device["gateway_last_octet"],
        netwatch_interval_sec=device["netwatch_interval_sec"],
        netwatch_timeout_sec=device["netwatch_timeout_sec"],
        router_state=state, device_id=device["id"],
    )
    if not plan.get("valid"):
        raise DeviceHealthError(plan.get("error") or "خطة غير صالحة.")

    applied: list[str] = []
    already: list[str] = []
    failed: list[dict] = []
    net = plan["network"]

    for item in plan["items"]:
        kind = item["kind"]
        if kind not in wanted:
            continue
        if item["action"] == "already_present":
            already.append(kind)
            _record_apply_state(tid, device, net, kind, "already_present", "")
            continue
        # action == 'create' → write it (gated, managed-by comment).
        res = _apply_one(mt, nas_dict, device, net, item)
        if res.ok:
            applied.append(kind)
            _record_apply_state(tid, device, net, kind, "applied", "",
                                mikrotik_id=str(res.data or ""))
            _audit(device, kind, ok=True)
        else:
            failed.append({"kind": kind, "error": res.error})
            _record_apply_state(tid, device, net, kind, "apply_failed", res.error)
            _audit(device, kind, ok=False, error=res.error)

    if failed:
        repo.set_status(tenant_id=tid, device_id=device["id"],
                        status="apply_failed")
        repo.add_event(tenant_id=tid, device_id=device["id"],
                       event_type="apply_failed", new_status="apply_failed",
                       message="فشل تطبيق بعض العناصر على الراوتر.")
    elif applied:
        repo.add_event(tenant_id=tid, device_id=device["id"],
                       event_type="updated", new_status=device["status"],
                       message="تم تطبيق خطة الوصول على الراوتر.")

    return {"ok": not failed, "gated": False, "applied": applied,
            "already_present": already, "failed": failed,
            "router_state_ok": state.get("ok", False)}


def _apply_one(mt, nas_dict, device, net, item):
    kind = item["kind"]
    if kind == "ip_address":
        return mt.add_ip_address(
            nas_dict, address=net["gateway_address"],
            interface=device["interface_name"], device_id=device["id"], live=True)
    if kind == "ip_binding":
        return mt.add_ip_binding(
            nas_dict, address=net["network_cidr"], binding_type="bypassed",
            device_id=device["id"], live=True)
    if kind == "netwatch":
        return mt.add_netwatch(
            nas_dict, host=net["ip_address"],
            interval_sec=device["netwatch_interval_sec"],
            timeout_sec=device["netwatch_timeout_sec"],
            device_id=device["id"], live=True)
    from .device_health_mikrotik import mac
    return mac.MtResult(ok=False, error=f"نوع غير معروف: {kind}")


def _record_apply_state(tid, device, net, kind, status, error, mikrotik_id=""):
    if kind == "ip_address":
        repo.set_scope_apply(
            tenant_id=tid, router_id=device["router_id"],
            interface_name=device["interface_name"],
            network_cidr=net["network_cidr"], apply_status=status,
            mikrotik_address_id=mikrotik_id, error=error)
    elif kind == "ip_binding":
        repo.set_binding_apply(
            tenant_id=tid, router_id=device["router_id"],
            network_cidr=net["network_cidr"], binding_type="bypassed",
            apply_status=status, mikrotik_binding_id=mikrotik_id, error=error)
    elif kind == "netwatch" and mikrotik_id:
        repo.set_netwatch_id(tid, device["id"], mikrotik_id)


def _audit(device, kind, *, ok: bool, error: str = "") -> None:
    """Record one audit-log entry per applied item. Never raises."""
    try:
        from .audit import get_audit_service
        get_audit_service().record(
            actor="device-health",
            action=f"device_health_apply:{kind}",
            target_type="network_device_monitor_device",
            target_id=str(device["id"]),
            router_id=device["router_id"],
            severity="info" if ok else "warning",
            result_status="success" if ok else "failed",
            error_message=error or "",
            payload={"interface": device["interface_name"],
                     "ip": device["ip_address"], "kind": kind},
        )
    except Exception:  # noqa: BLE001 — audit must never break apply
        pass


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
