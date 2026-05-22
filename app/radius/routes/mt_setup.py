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
    render_wg_block,
)
from ..services import wg_peer_manager as wpm


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
    # O2 — pass an api_token so the per-row counter poll JS can
    # authenticate against /api/v1/mikrotik/<id>/counters without
    # needing a separate session-bridging step.
    from .mt_dashboard import _ui_api_token   # internal helper reuse
    return render_template(
        "radius/mt_operations.html",
        items=items,
        api_token=_ui_api_token(),
    )


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
    if ros_version not in SUPPORTED_ROS_VERSIONS:
        flash("اختر نسخة RouterOS (6 أو 7)", "error")
        return redirect(url_for("radius.mt_setup_form"))
    # For v6 the operator MUST supply an address — RouterOS 6 has
    # no native WireGuard, so we can't auto-provision a tunnel.
    if ros_version == "6" and not address:
        flash(
            "RouterOS 6 لا يدعم WireGuard — اكتب عنوان الراوتر (IP) "
            "للاتصال المباشر",
            "error",
        )
        return redirect(url_for("radius.mt_setup_form"))
    if ros_version == "7" and not server_ip:
        flash(
            "لم نتمكّن من معرفة عنوان السيرفر — اكتبه يدويًّا أو اضبط "
            "HOBERADIUS_PUBLIC_IP",
            "error",
        )
        return redirect(url_for("radius.mt_setup_form"))

    creds = generate_credentials()

    # Phase M — for RouterOS 7 we auto-provision a WireGuard peer
    # so the router never needs a public IP. The NAS row records
    # the tunnel address as its `address`, and HobeRadius dials
    # the router via that IP (connection_mode='vpn').
    wg_provision = None
    if ros_version == "7":
        try:
            wg_provision = wpm.provision_peer(name)
        except ValueError as exc:
            flash(f"تعذّر تجهيز WireGuard: {exc}", "error")
            return redirect(url_for("radius.mt_setup_form"))
        except Exception as exc:  # noqa: BLE001
            flash(
                "WG غير مهيّأ على السيرفر بعد — تأكّد أن "
                "HOBERADIUS_WG_SERVER_PUBKEY و HOBERADIUS_WG_SERVER_ENDPOINT "
                f"مضبوطين في .env. ({exc})",
                "error",
            )
            return redirect(url_for("radius.mt_setup_form"))
        # Override the operator-typed address with the WG-allocated one.
        address = str(wg_provision.allowed_ip)

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
        # If we already created a WG peer, roll it back so the IP
        # is reclaimed.
        if wg_provision is not None:
            try:
                wpm.deprovision_peer(wg_provision.slug)
            except Exception:
                pass
        flash(f"فشل إنشاء صف الراوتر: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    # Backfill L2 columns + (M2) the K1 VPN columns +
    # (M4) sync into the FreeRADIUS `nas` table so the router can
    # actually RADIUS-authenticate. FreeRADIUS reads this table on
    # boot with `read_clients = yes`; new rows are picked up by
    # restarting freeradius (or sending HUP).
    now = datetime.now(timezone.utc).isoformat() + "Z"
    short_name = (saved.name or "rt")[:32]
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version=?, provisioned_at=? "
            "WHERE id=? AND tenant_id=?",
            (ros_version, now, saved.id, _tid()),
        )
        if wg_provision is not None:
            c.execute(
                "UPDATE nas_devices SET connection_mode=?, "
                "       vpn_peer_address=?, vpn_public_key=? "
                "WHERE id=? AND tenant_id=?",
                (
                    "vpn",
                    str(wg_provision.allowed_ip),
                    wg_provision.router_public_key,
                    saved.id, _tid(),
                ),
            )
        # M4 — make the router visible to FreeRADIUS. The `nas`
        # table has no unique constraint on (tenant_id, nasname),
        # so we delete-then-insert to stay idempotent on re-runs
        # (e.g. operator deletes a NAS and re-creates with the
        # same address — fresh secret, fresh row).
        c.execute(
            "DELETE FROM nas WHERE tenant_id=? AND nasname=?",
            (_tid(), saved.address),
        )
        c.execute(
            "INSERT INTO nas (tenant_id, nasname, shortname, type, secret) "
            "VALUES (?,?,?,?,?)",
            (_tid(), saved.address, short_name, "mikrotik", saved.secret),
        )

    # The router's PRIVATE key only exists in this request. It
    # belongs on the router, never in our DB. Stash it on the
    # session for the next-page render and delete it after one use.
    if wg_provision is not None:
        session[f"_wg_router_priv_{saved.id}"] = wg_provision.router_private_key

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
    ros_version = (nas.get("ros_version") or "7").strip()

    # Phase M — for v7+VPN rows we render the WG block in front of
    # the RADIUS commands. The router's private key only lives in
    # the session for one render (see mt_setup_create), so a
    # refresh after issuing intentionally gives a "key already
    # shown — rotate to re-issue" notice rather than re-printing
    # the secret.
    wg_block = None
    wg_priv_revealed = False
    wg_priv = session.pop(f"_wg_router_priv_{nas_id}", None)
    is_vpn_row = (nas.get("connection_mode") or "").strip().lower() == "vpn"

    if ros_version == "7" and is_vpn_row:
        try:
            cfg = wpm.load_config()
        except ValueError as exc:
            flash(
                f"تعذّر قراءة إعدادات WireGuard من البيئة: {exc}",
                "error",
            )
            return redirect(url_for("radius.mt_setup_form"))
        if wg_priv:
            wg_priv_revealed = True
            wg_block = render_wg_block(
                nas_name=nas["name"],
                router_private_key=wg_priv,
                server_pubkey=cfg.server_pubkey,
                server_endpoint=cfg.server_endpoint,
                allowed_subnet=str(cfg.subnet),
                router_tunnel_ip=f"{nas['address']}/{cfg.subnet.prefixlen}",
                keepalive_sec=wpm.DEFAULT_KEEPALIVE_SEC,
                ros_version="7",
            )
        # else: no private key in session → leave wg_block None.
        # The template surfaces a "private key already issued" notice.

    # For the RADIUS half of the script, `server_ip` is what the
    # router will dial. For VPN rows that's the server's tunnel IP
    # (10.10.0.1), for direct rows it's the operator-supplied IP
    # from ?server_ip=… .
    if is_vpn_row:
        try:
            cfg = wpm.load_config()
            radius_server_ip = str(cfg.server_ip)
        except ValueError:
            radius_server_ip = (
                request.args.get("server_ip") or _default_server_ip() or "<SERVER_IP>"
            )
    else:
        radius_server_ip = (
            request.args.get("server_ip") or _default_server_ip() or "<SERVER_IP>"
        )

    # For VPN rows, lock the router's API service to the WG subnet
    # so the API can't be reached from the LAN/public interfaces.
    # Direct-mode rows leave it open (operator firewalls as needed).
    api_allowed_address = None
    if is_vpn_row:
        try:
            api_allowed_address = str(wpm.load_config().subnet)
        except ValueError:
            api_allowed_address = None
    try:
        script = render_routeros_script(
            nas_name=nas["name"],
            api_user=nas["api_user"],
            api_password=nas["api_password"],
            radius_secret=nas["secret"],
            server_ip=radius_server_ip,
            ros_version=ros_version,
            api_port=int(nas.get("api_port") or 8728),
            coa_port=int(nas.get("coa_port") or 3799),
            wg_block=wg_block,
            api_allowed_address=api_allowed_address,
        )
    except ValueError as exc:
        flash(f"تعذّر توليد السكربت: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    return render_template(
        "radius/mt_setup_script.html",
        nas=nas,
        script=script,
        server_ip=radius_server_ip,
        ros_version=ros_version,
        is_vpn_row=is_vpn_row,
        wg_priv_revealed=wg_priv_revealed,
        dashboard_url=url_for("radius.mt_dashboard", nas_id=nas["id"]),
    )
