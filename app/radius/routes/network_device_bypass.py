"""Device Bypass routes — Sprint 3.

  GET  /admin/radius/network/devices/<id>/bypass         — form
  POST /admin/radius/network/devices/<id>/bypass         — apply
  POST /admin/radius/network/devices/<id>/bypass/remove  — cleanup
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import nas_repo, network_devices_repo
from ..services import network_device_bypass_planner as bypass


def register_network_device_bypass_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/network/devices/<int:device_id>/bypass",
        "network_device_bypass_form",
        network_device_bypass_form, methods=["GET"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/bypass",
        "network_device_bypass_apply",
        network_device_bypass_apply, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/bypass/remove",
        "network_device_bypass_remove",
        network_device_bypass_remove, methods=["POST"],
    )


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _load_pair(device_id: int):
    """Common prologue: resolve device + its router, abort if
    either is missing. Returns (device, nas)."""
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    nas = nas_repo.get_nas(tenant_id, device["router_id"])
    if not nas:
        abort(404)
    # nas comes back as a dataclass — flatten to dict for the
    # planner's MikrotikClient call (matches every other call
    # site that uses `_safe_dial`).
    return device, _nas_to_dict(nas)


def _nas_to_dict(nas_dc) -> dict:
    return {
        "id":            nas_dc.id,
        "tenant_id":     nas_dc.tenant_id,
        "name":          nas_dc.name,
        "address":       nas_dc.address,
        "api_port":      nas_dc.api_port,
        "api_user":      nas_dc.api_user,
        "api_password":  nas_dc.api_password,
        "api_use_tls":   nas_dc.api_use_tls,
        "api_timeout_sec": getattr(nas_dc, "api_timeout_sec", 3) or 3,
    }


# ── Views ──────────────────────────────────────────────────────


def network_device_bypass_form(device_id: int):
    device, nas = _load_pair(device_id)
    # Pull the live list of DHCP servers from the router so the
    # operator picks an existing name (no typos).
    dhcp_result = bypass.list_dhcp_servers(nas)
    dhcp_servers = dhcp_result.data if (dhcp_result.ok and dhcp_result.data) else []
    return render_template(
        "radius/network_device_bypass.html",
        device=device,
        nas=nas,
        dhcp_servers=dhcp_servers,
        dhcp_error=(None if dhcp_result.ok else dhcp_result.error),
    )


def network_device_bypass_apply(device_id: int):
    device, nas = _load_pair(device_id)
    if not device["mac_address"] or not device["ip_address"]:
        flash(
            "هذا الجهاز يحتاج MAC + IP محفوظَين في الـ Registry قبل التطبيق.",
            "danger",
        )
        return redirect(url_for(
            "radius.network_devices_edit", device_id=device_id,
        ))
    dhcp_server_name = (request.form.get("dhcp_server_name") or "").strip()
    bypass_hotspot = request.form.get("bypass_hotspot", "") in ("1", "on", "true")
    add_to_address_list = request.form.get("add_to_address_list", "") in ("1", "on", "true")
    if not dhcp_server_name:
        flash("اختر اسم DHCP server على الراوتر.", "danger")
        return redirect(url_for(
            "radius.network_device_bypass_form", device_id=device_id,
        ))
    result = bypass.apply_bypass(
        nas=nas,
        device=device,
        dhcp_server_name=dhcp_server_name,
        bypass_hotspot=bypass_hotspot,
        add_to_address_list=add_to_address_list,
    )
    if not result.ok:
        flash(f"فشل الاتصال بالراوتر: {result.error}", "danger")
        return redirect(url_for(
            "radius.network_device_bypass_form", device_id=device_id,
        ))
    # Render result detail as a flash so the operator sees per-
    # step outcomes (some steps may have «failed: already exists»
    # which is harmless).
    steps = result.data or {}
    parts = []
    labels = {
        "dhcp_lease":   "DHCP lease",
        "ip_binding":   "IP binding",
        "address_list": "address-list",
    }
    for key, label in labels.items():
        if key in steps:
            parts.append(f"{label}: {steps[key]}")
    flash(
        f"تم تنفيذ التجهيز على «{device['name']}» — {' | '.join(parts)}",
        "success",
    )
    return redirect(url_for("radius.network_devices_list"))


def network_device_bypass_remove(device_id: int):
    device, nas = _load_pair(device_id)
    result = bypass.remove_bypass(nas=nas, device_id=device_id)
    if not result.ok:
        flash(f"فشل الاتصال بالراوتر: {result.error}", "danger")
    else:
        removed = result.data or {}
        total = sum(removed.values()) if isinstance(removed, dict) else 0
        flash(
            f"حُذف التجهيز عن «{device['name']}» — تم إزالة {total} سطر من الراوتر.",
            "success",
        )
    return redirect(url_for("radius.network_devices_list"))
