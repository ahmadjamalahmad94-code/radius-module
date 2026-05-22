"""Q1 — Network programming wizard (read-only generator).

Routes:
  GET  /admin/radius/mt/<id>/program        — show the form.
  POST /admin/radius/mt/<id>/program/plan   — render the plan.

Apply is intentionally *not* a route in Q1 — the apply path
ships in Q2 behind a confirmation modal + audit log. The form
view here therefore renders the plan inside the same page with
the apply button disabled and a hint pointing to Q2.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..services import mt_programming
from ..services import mikrotik_admin_client as mac


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, api_port, api_user, api_password, "
        "       api_use_tls, enabled, connection_mode, "
        "       vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    return dict(row) if row else None


def _nas_for_mac(nas: dict) -> dict:
    """Translate a nas_devices row into the dict shape the admin
    client expects (mt_diagnostics uses the same pattern)."""
    return {
        "id":          nas["id"],
        "name":        nas["name"],
        "host":        nas["address"],
        "port":        int(nas.get("api_port") or 8728),
        "username":    nas.get("api_user") or "admin",
        "password":    nas.get("api_password") or "",
        "use_tls":     bool(nas.get("api_use_tls")),
        "verify_tls":  True,
        "timeout_sec": 10,
    }


def register_mt_programming_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/program",
        "mt_program_form",
        mt_program_form,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/program/plan",
        "mt_program_plan",
        mt_program_plan,
        methods=["POST"],
    )


def _fetch_router_state(nas: dict) -> tuple[list[dict], list[dict]]:
    """Pull the router's current interfaces + IP addresses so the
    planner can surface conflicts. Failure is non-fatal — the plan
    still generates, the conflict block just won't show specifics.
    """
    nas_call = _nas_for_mac(nas)
    iface_res = mac.interface_list(nas_call)
    addr_res  = mac.ip_addresses(nas_call)
    return (
        list(iface_res.data) if iface_res.ok else [],
        list(addr_res.data)  if addr_res.ok else [],
    )


def mt_program_form(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    return render_template(
        "radius/mt_programming.html",
        nas=nas,
        plan=None,
        form={},
    )


def mt_program_plan(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    form = {
        "interface":    (request.form.get("interface")    or "").strip(),
        "cidr":         (request.form.get("cidr")         or "").strip(),
        "hotspot_name": (request.form.get("hotspot_name") or "").strip(),
        "dns_servers":  (request.form.get("dns_servers")
                         or "8.8.8.8,1.1.1.1").strip(),
        "pool_start":   (request.form.get("pool_start")   or "").strip(),
        "pool_end":     (request.form.get("pool_end")     or "").strip(),
        "gateway":      (request.form.get("gateway")      or "").strip(),
        "lease_time":   (request.form.get("lease_time")   or "1h").strip(),
        "rate_limit":   (request.form.get("rate_limit")   or "").strip(),
    }
    spec = mt_programming.HotspotProgrammingSpec(
        interface=form["interface"],
        cidr=form["cidr"],
        hotspot_name=form["hotspot_name"],
        dns_servers=form["dns_servers"],
        pool_start=form["pool_start"],
        pool_end=form["pool_end"],
        gateway=form["gateway"],
        lease_time=form["lease_time"],
        rate_limit=form["rate_limit"],
    )
    error: str = ""
    plan = None
    try:
        ifaces, addrs = _fetch_router_state(nas)
        plan = mt_programming.plan_hotspot(
            nas, spec,
            existing_interfaces=ifaces,
            existing_addresses=addrs,
        )
    except ValueError as e:
        error = str(e)
    return render_template(
        "radius/mt_programming.html",
        nas=nas,
        plan=plan,
        form=form,
        error=error,
    )
