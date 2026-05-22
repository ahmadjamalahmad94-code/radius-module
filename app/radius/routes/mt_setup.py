"""L3 — MikroTik setup-wizard routes.

Three endpoints back the Phase-L wizard flow:

    GET  /admin/radius/mt/setup
        Form: nas_name + address + RouterOS major version + (auto)
        server IP. Submits to POST below.

    POST /admin/radius/mt/setup
        - Generates fresh credentials (mt_provisioner).
        - Writes nas_devices row through the existing service so
          the audit trail captures the creation.
        - Backfills the L2 columns (ros_version, provisioned_at).
        - Redirects to the script page with the new nas_id +
          server_ip carried as a query arg.

    GET  /admin/radius/mt/<nas_id>/script?server_ip=...
        Renders the RouterOS script for that row. The page itself
        has a 'Test now' button that just deep-links into the
        existing K9 dashboard — there's no separate test endpoint
        to write because /system/overview already does the job.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request,
    session, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import NasDevice
from ..db.connection import db, transaction
from ..services.devices import get_nas_devices_service
from ..services.mt_provisioner import (
    SUPPORTED_ROS_VERSIONS, generate_credentials, render_routeros_script,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "wizard"


def _default_server_ip() -> str:
    """Best-effort guess of the address the RADIUS server is reachable
    at FROM the router's perspective.

    Priority:
      1. `HOBERADIUS_PUBLIC_IP` env (operator-set, authoritative).
      2. The request's host header without port (works when the
         admin uses the same address the router would).
      3. Empty string — the form shows a placeholder asking the
         operator to type it.
    """
    env_ip = (os.environ.get("HOBERADIUS_PUBLIC_IP") or "").strip()
    if env_ip:
        return env_ip
    try:
        return (request.host or "").split(":", 1)[0]
    except RuntimeError:
        return ""


def register_mt_setup_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/operations", "mt_operations", mt_operations, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/setup", "mt_setup_form", mt_setup_form, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/setup", "mt_setup_create", mt_setup_create, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/script",
        "mt_setup_script", mt_setup_script, methods=["GET"],
    )


def mt_operations():
    """L5 — Operations Center.

    Single source of truth for 'which routers does this HobeRadius
    talk to?'. Reads nas_devices (excluding soft-deleted), shows a
    sequential display number (#1, #2, ...) for the operator while
    keeping the real DB id stable in the row's dashboard link.
    """
    rows = db().execute(
        "SELECT id, name, address, enabled, ros_version, "
        "       provisioned_at, last_check_status, last_check_at, "
        "       connection_mode, api_user, api_port "
        "FROM nas_devices "
        "WHERE tenant_id=? AND (deleted_at IS NULL OR deleted_at='') "
        "ORDER BY id ASC",
        (_tid(),),
    ).fetchall()
    items = []
    for n, row in enumerate(rows, start=1):
        items.append({
            "display_num": n,
            "id": row["id"],
            "name": row["name"],
            "address": row["address"],
            "enabled": bool(row["enabled"]),
            "ros_version": row["ros_version"] or "",
            "provisioned_at": row["provisioned_at"] or "",
            "last_check_status": row["last_check_status"] or "",
            "last_check_at": row["last_check_at"] or "",
            "connection_mode": row["connection_mode"] or "direct",
            "api_user": row["api_user"] or "",
            "api_port": row["api_port"] or 8728,
        })
    return render_template("radius/mt_operations.html", items=items)


def mt_setup_form():
    return render_template(
        "radius/mt_setup_form.html",
        default_server_ip=_default_server_ip(),
        ros_versions=SUPPORTED_ROS_VERSIONS,
    )


def mt_setup_create():
    name = (request.form.get("name") or "").strip()
    address = (request.form.get("address") or "").strip()
    ros_version = (request.form.get("ros_version") or "").strip()
    server_ip = (request.form.get("server_ip") or _default_server_ip()).strip()

    # Hard validation. Friendly form-level checks live in the
    # template; this is the last line of defence.
    if not name:
        flash("اكتب اسمًا للراوتر", "error")
        return redirect(url_for("radius.mt_setup_form"))
    if not address:
        flash("اكتب عنوان الراوتر (IP)", "error")
        return redirect(url_for("radius.mt_setup_form"))
    if ros_version not in SUPPORTED_ROS_VERSIONS:
        flash("اختر نسخة RouterOS (6 أو 7)", "error")
        return redirect(url_for("radius.mt_setup_form"))
    if not server_ip:
        flash("لم نتمكّن من معرفة عنوان السيرفر — اكتبه يدويًّا", "error")
        return redirect(url_for("radius.mt_setup_form"))

    creds = generate_credentials()
    dev = NasDevice(
        id=None,
        name=name,
        address=address,
        secret=creds["radius_secret"],
        vendor="mikrotik",
        nas_type="hotspot",
        api_port=8728,
        api_user=creds["api_user"],
        api_password=creds["api_password"],
        api_use_tls=False,
        enabled=True,
        monitoring_enabled=True,
    )
    try:
        saved = get_nas_devices_service().create(actor=_actor(), device=dev)
    except Exception as exc:  # noqa: BLE001
        flash(f"فشل إنشاء صف الراوتر: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    # Backfill L2 columns. The NasDevice dataclass doesn't expose
    # them today — the service writes the standard fields; we then
    # add the wizard-specific ones with a direct UPDATE.
    now = datetime.now(timezone.utc).isoformat() + "Z"
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version=?, provisioned_at=? "
            "WHERE id=? AND tenant_id=?",
            (ros_version, now, saved.id, _tid()),
        )

    return redirect(url_for(
        "radius.mt_setup_script",
        nas_id=saved.id,
        server_ip=server_ip,
    ))


def mt_setup_script(nas_id: int):
    row = db().execute(
        "SELECT * FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    server_ip = (request.args.get("server_ip") or _default_server_ip()).strip()
    # The wizard always writes a version; legacy rows may have an
    # empty ros_version. Fall back to v7 (the current default) so
    # the script page never errors on those edge cases.
    ros_version = (nas.get("ros_version") or "7").strip()
    try:
        script = render_routeros_script(
            nas_name=nas["name"],
            api_user=nas["api_user"],
            api_password=nas["api_password"],
            radius_secret=nas["secret"],
            server_ip=server_ip or "<SERVER_IP>",
            ros_version=ros_version,
            api_port=int(nas.get("api_port") or 8728),
            coa_port=int(nas.get("coa_port") or 3799),
        )
    except ValueError as exc:
        flash(f"تعذّر توليد السكربت: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    return render_template(
        "radius/mt_setup_script.html",
        nas=nas,
        script=script,
        server_ip=server_ip,
        ros_version=ros_version,
        dashboard_url=url_for("radius.mt_dashboard", nas_id=nas["id"]),
    )
