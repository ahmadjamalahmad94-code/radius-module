"""Network Devices routes — Sprint 1 (Device Registry).

Backs the «تابع أجهزة الشبكة» page:
  GET  /admin/radius/network/devices                 — list
  GET  /admin/radius/network/devices/new             — form
  POST /admin/radius/network/devices                 — create
  GET  /admin/radius/network/devices/<id>/edit       — form
  POST /admin/radius/network/devices/<id>            — update
  POST /admin/radius/network/devices/<id>/delete     — delete
  POST /admin/radius/network/devices/<id>/check      — manual TCP probe

Per NETWORK_OPERATIONS_PLAN.md sprint 1: NO periodic polling,
NO alerts, NO router-side rules. The manual «فحص الآن» button
just does a TCP-connect against the device's management port
and updates `last_status`. Sprint 2 will replace it with a
cron job and add the check-history table.
"""
from __future__ import annotations

import socket
import time
from typing import Any

from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import nas_repo, network_devices_repo


# ── Route registration ─────────────────────────────────────────


def register_network_devices_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/network/devices",
        "network_devices_list",
        network_devices_list, methods=["GET"],
    )
    bp.add_url_rule(
        "/network/devices/new",
        "network_devices_new",
        network_devices_new, methods=["GET"],
    )
    bp.add_url_rule(
        "/network/devices",
        "network_devices_create",
        network_devices_create, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/edit",
        "network_devices_edit",
        network_devices_edit, methods=["GET"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>",
        "network_devices_update",
        network_devices_update, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/delete",
        "network_devices_delete",
        network_devices_delete, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/check",
        "network_devices_check",
        network_devices_check, methods=["POST"],
    )


# ── Helpers ────────────────────────────────────────────────────


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _s(name: str, default: str = "") -> str:
    return (request.form.get(name) or default).strip()


def _i(name: str, default: int) -> int:
    try:
        return int(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _b(name: str) -> bool:
    return request.form.get(name, "") in ("1", "on", "true", "yes")


def _routers_for_dropdown(tenant_id: int) -> list[dict]:
    """Lightweight list of {id, name, address} for the «الراوتر»
    dropdown in the form. Reuses nas_repo to honour soft-delete."""
    rows = nas_repo.list_nas(tenant_id, limit=500)
    return [{"id": n.id, "name": n.name, "address": n.address}
            for n in rows]


# ── Views ──────────────────────────────────────────────────────


def network_devices_list():
    tenant_id = _tid()
    devices = network_devices_repo.list_for_tenant(tenant_id)
    routers = _routers_for_dropdown(tenant_id)
    # Pre-build {router_id → name} so the template can show the
    # router name next to each device without a per-row lookup.
    router_names = {r["id"]: r["name"] for r in routers}
    return render_template(
        "radius/network_devices_list.html",
        devices=devices,
        routers=routers,
        router_names=router_names,
    )


def network_devices_new():
    tenant_id = _tid()
    routers = _routers_for_dropdown(tenant_id)
    return render_template(
        "radius/network_devices_form.html",
        device=None,
        routers=routers,
    )


def network_devices_create():
    tenant_id = _tid()
    router_id = _i("router_id", 0)
    if not router_id:
        flash("اختر الراوتر الذي يتبع له الجهاز.", "danger")
        return redirect(url_for("radius.network_devices_new"))
    # Guard — make sure the router belongs to this tenant.
    if not nas_repo.get_nas(tenant_id, router_id):
        flash("الراوتر المُختار غير موجود.", "danger")
        return redirect(url_for("radius.network_devices_new"))
    name = _s("name")
    if not name:
        flash("اسم الجهاز مطلوب.", "danger")
        return redirect(url_for("radius.network_devices_new"))
    new_id = network_devices_repo.create(
        tenant_id=tenant_id,
        router_id=router_id,
        name=name,
        device_type=_s("device_type", "other"),
        ip_address=_s("ip_address"),
        mac_address=_s("mac_address"),
        location=_s("location"),
        management_port=_i("management_port", 80),
        notes=_s("notes"),
        is_critical=_b("is_critical"),
        watch_enabled=_b("watch_enabled"),
        alert_enabled=_b("alert_enabled"),
    )
    flash(f"أُضيف الجهاز «{name}» (رقم {new_id}).", "success")
    return redirect(url_for("radius.network_devices_list"))


def network_devices_edit(device_id: int):
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    routers = _routers_for_dropdown(tenant_id)
    return render_template(
        "radius/network_devices_form.html",
        device=device,
        routers=routers,
    )


def network_devices_update(device_id: int):
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    # router_id stays editable so the operator can move a device
    # if they re-cabled it. Validate the target router exists.
    new_router_id = _i("router_id", device["router_id"])
    if new_router_id != device["router_id"]:
        if not nas_repo.get_nas(tenant_id, new_router_id):
            flash("الراوتر المُختار غير موجود.", "danger")
            return redirect(url_for(
                "radius.network_devices_edit", device_id=device_id,
            ))
    fields = {
        "name":            _s("name", device["name"]),
        "device_type":     _s("device_type", device["device_type"]),
        "ip_address":      _s("ip_address", device["ip_address"]),
        "mac_address":     _s("mac_address", device["mac_address"]),
        "location":        _s("location", device["location"]),
        "management_port": _i("management_port", device["management_port"]),
        "notes":           _s("notes", device["notes"]),
        "is_critical":     _b("is_critical"),
        "watch_enabled":   _b("watch_enabled"),
        "alert_enabled":   _b("alert_enabled"),
    }
    if not fields["name"]:
        flash("اسم الجهاز مطلوب.", "danger")
        return redirect(url_for(
            "radius.network_devices_edit", device_id=device_id,
        ))
    # router_id is its own column — update_repo doesn't whitelist it
    # so we issue a tiny direct update for that field only.
    if new_router_id != device["router_id"]:
        from ..db.connection import db, transaction
        from ..db.helpers import now_iso
        with transaction() as conn:
            conn.execute(
                "UPDATE network_devices "
                "SET router_id = ?, updated_at = ? "
                "WHERE tenant_id = ? AND id = ?",
                (new_router_id, now_iso(), tenant_id, device_id),
            )
    network_devices_repo.update(tenant_id, device_id, **fields)
    flash("تم حفظ التعديلات.", "success")
    return redirect(url_for("radius.network_devices_list"))


def network_devices_delete(device_id: int):
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    network_devices_repo.delete(tenant_id, device_id)
    flash(f"حُذف الجهاز «{device['name']}».", "success")
    return redirect(url_for("radius.network_devices_list"))


def network_devices_check(device_id: int):
    """Manual «فحص الآن» — TCP-connect probe to the device's
    management port. Sprint 1 is HobeRadius-side only; if the
    VPS can't reach the LAN (no WG route yet) the probe just
    times out and the row flips to `down`.
    """
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    ip = device["ip_address"]
    port = device["management_port"]
    if not ip:
        flash("لا يمكن الفحص — IP الجهاز فارغ.", "danger")
        return redirect(url_for("radius.network_devices_list"))
    status, latency_ms = _tcp_probe(ip, port)
    network_devices_repo.set_last_check(
        tenant_id=tenant_id,
        device_id=device_id,
        status=status,
        latency_ms=latency_ms,
    )
    if status == "up":
        flash(
            f"الجهاز «{device['name']}» يستجيب — {latency_ms:.1f} ms",
            "success",
        )
    else:
        flash(
            f"تعذّر الوصول للجهاز «{device['name']}» — لا يوجد ردّ على {ip}:{port}",
            "warning",
        )
    return redirect(url_for("radius.network_devices_list"))


def _tcp_probe(host: str, port: int, timeout_sec: float = 2.0) -> tuple[str, float | None]:
    """One-shot reachability probe. Returns ('up', ms) on success
    or ('down', None) on any error/timeout. Sprint 1 keeps it
    here; Sprint 2 will lift the logic into the cron worker."""
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout_sec)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return "up", round(elapsed_ms, 1)
    except (socket.timeout, OSError):
        return "down", None
    finally:
        if sock is not None:
            try: sock.close()
            except OSError: pass


# Touch-the-tenant guard for the lint/test eye — these names
# aren't used directly but importing them at module top would be
# fine. Kept here so a future audit grep sees them.
_ = Any
