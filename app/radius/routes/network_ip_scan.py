"""IP Scan routes — Sprint 4.

  GET  /admin/radius/network/scan           — empty page + router picker
  POST /admin/radius/network/scan           — run scan on chosen router
  POST /admin/radius/network/scan/add       — register a discovered device
"""
from __future__ import annotations

from flask import (
    Blueprint, flash, g, redirect, render_template,
    request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import nas_repo, network_devices_repo
from ..services import network_ip_scan


def register_network_ip_scan_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/network/scan",
        "network_ip_scan_page",
        network_ip_scan_page, methods=["GET", "POST"],
    )
    bp.add_url_rule(
        "/network/scan/add",
        "network_ip_scan_add",
        network_ip_scan_add, methods=["POST"],
    )


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _nas_dict(nas_dc) -> dict:
    return {
        "id":              nas_dc.id,
        "tenant_id":       nas_dc.tenant_id,
        "name":            nas_dc.name,
        "address":         nas_dc.address,
        "api_port":        nas_dc.api_port,
        "api_user":        nas_dc.api_user,
        "api_password":    nas_dc.api_password,
        "api_use_tls":     nas_dc.api_use_tls,
        "api_timeout_sec": getattr(nas_dc, "api_timeout_sec", 3) or 3,
    }


def network_ip_scan_page():
    tenant_id = _tid()
    routers = nas_repo.list_nas(tenant_id, limit=500)

    selected_router_id: int | None = None
    scan_rows: list[dict] = []
    scan_error: str | None = None
    selected_router_name = ""

    if request.method == "POST":
        try:
            selected_router_id = int(request.form.get("router_id") or 0)
        except ValueError:
            selected_router_id = 0
        if selected_router_id:
            nas_dc = nas_repo.get_nas(tenant_id, selected_router_id)
            if not nas_dc:
                flash("الراوتر غير موجود.", "danger")
                return redirect(url_for("radius.network_ip_scan_page"))
            selected_router_name = nas_dc.name
            result = network_ip_scan.scan_router(_nas_dict(nas_dc))
            if not result.ok:
                scan_error = result.error or "تعذّر الاتصال بالراوتر."
            else:
                scan_rows = result.data or []

    # Cross-reference the registry so the UI can mark «known»
    # IPs (already in network_devices) — only when we have a
    # router selected.
    known_ips: set[str] = set()
    if selected_router_id:
        known_ips = {
            d["ip_address"] for d in network_devices_repo.list_for_tenant(
                tenant_id, router_id=selected_router_id,
            ) if d.get("ip_address")
        }

    return render_template(
        "radius/network_ip_scan.html",
        routers=routers,
        selected_router_id=selected_router_id,
        selected_router_name=selected_router_name,
        scan_rows=scan_rows,
        scan_error=scan_error,
        known_ips=known_ips,
    )


def network_ip_scan_add():
    """One-click «add to watch» for a discovered row."""
    tenant_id = _tid()
    try:
        router_id = int(request.form.get("router_id") or 0)
    except ValueError:
        router_id = 0
    ip = (request.form.get("ip") or "").strip()
    mac = (request.form.get("mac") or "").strip()
    hostname = (request.form.get("hostname") or "").strip()
    if not router_id or not ip:
        flash("بيانات ناقصة.", "danger")
        return redirect(url_for("radius.network_ip_scan_page"))
    if not nas_repo.get_nas(tenant_id, router_id):
        flash("الراوتر غير موجود.", "danger")
        return redirect(url_for("radius.network_ip_scan_page"))
    # Use the hostname (if any) as a starting name; operator can
    # rename later from the edit form.
    name = hostname or f"جهاز {ip}"
    new_id = network_devices_repo.create(
        tenant_id=tenant_id,
        router_id=router_id,
        name=name,
        device_type="other",
        ip_address=ip,
        mac_address=mac,
        watch_enabled=True,  # added from a scan → likely wants monitoring
    )
    flash(
        f"أُضيف الجهاز «{name}» للسجلّ (رقم {new_id}). "
        f"عدّل اسمه ونوعه من صفحة «تابع أجهزة الشبكة».",
        "success",
    )
    return redirect(url_for("radius.network_ip_scan_page",
                            router_id=router_id))
