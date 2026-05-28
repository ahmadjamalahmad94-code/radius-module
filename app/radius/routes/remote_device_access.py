"""Remote Device Access routes — Sprint 5."""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, session as flask_session, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import (
    nas_repo,
    network_devices_repo,
    remote_access_sessions_repo,
)
from ..services import remote_device_access


def register_remote_device_access_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/network/devices/<int:device_id>/remote",
        "remote_device_access_form",
        remote_device_access_form, methods=["GET"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/remote/open",
        "remote_device_access_open",
        remote_device_access_open, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/remote/<int:session_id>/close",
        "remote_device_access_close",
        remote_device_access_close, methods=["POST"],
    )


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return (flask_session.get("admin_name")
            or flask_session.get("admin_user")
            or "anonymous")


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


def _load_pair(device_id: int):
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    nas_dc = nas_repo.get_nas(tenant_id, device["router_id"])
    if not nas_dc:
        abort(404)
    return device, _nas_dict(nas_dc)


def remote_device_access_form(device_id: int):
    device, nas = _load_pair(device_id)
    sessions = remote_access_sessions_repo.list_for_device(
        _tid(), device_id, limit=15,
    )
    return render_template(
        "radius/remote_device_access.html",
        device=device,
        nas=nas,
        sessions=sessions,
    )


def remote_device_access_open(device_id: int):
    device, nas = _load_pair(device_id)
    if not device["ip_address"]:
        flash("لا يمكن فتح الجلسة — IP الجهاز فارغ.", "danger")
        return redirect(url_for(
            "radius.remote_device_access_form", device_id=device_id,
        ))
    protocol = (request.form.get("protocol") or "http").strip()
    try:
        ttl_minutes = int(request.form.get("ttl_minutes") or 30)
    except ValueError:
        ttl_minutes = 30
    # Cap the TTL so a typo can't open a port for a week.
    ttl_minutes = max(5, min(ttl_minutes, 240))
    notes = (request.form.get("notes") or "").strip()

    ok, err, session = remote_device_access.open_session(
        nas=nas,
        device=device,
        requested_by=_actor(),
        ttl_minutes=ttl_minutes,
        protocol=protocol,
        audit_ip=(request.remote_addr or "")[:64],
        notes=notes,
    )
    if not ok or not session:
        flash(f"فشل فتح الجلسة: {err}", "danger")
        return redirect(url_for(
            "radius.remote_device_access_form", device_id=device_id,
        ))
    flash(
        f"تم فتح الجلسة #{session['id']} — تنتهي خلال {ttl_minutes} دقيقة.",
        "success",
    )
    return redirect(url_for(
        "radius.remote_device_access_form", device_id=device_id,
    ))


def remote_device_access_close(device_id: int, session_id: int):
    device, nas = _load_pair(device_id)
    session = remote_access_sessions_repo.get(_tid(), session_id)
    if not session or session["device_id"] != device_id:
        abort(404)
    if session["status"] != "active":
        flash("هذه الجلسة مغلقة بالفعل.", "info")
        return redirect(url_for(
            "radius.remote_device_access_form", device_id=device_id,
        ))
    ok, warn = remote_device_access.close_session(
        nas=nas, session=session, status="closed",
    )
    if warn:
        flash(warn, "warning")
    else:
        flash(f"أُغلقت الجلسة #{session_id}.", "success")
    return redirect(url_for(
        "radius.remote_device_access_form", device_id=device_id,
    ))
