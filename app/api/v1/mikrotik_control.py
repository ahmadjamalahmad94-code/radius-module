"""K3 + K4 — MikroTik control endpoints (system + network).

This file ships the live-operations endpoints that the K9 dashboard
and the K10 sub-pages consume. Every call:

1. Looks up the `nas_devices` row by id (admin scoped to tenant).
2. Pipes it through a `mikrotik_admin_client.*` fetcher — which
   honours the VPN connection mode, applies the TTL cache, and
   wraps every error in a clean envelope.
3. Returns JSON `{ok, data, error, took_ms, cached, dialed_address,
   mode}`.

K3 endpoints — system stats:
  GET /api/v1/mikrotik/<id>/system/resource
  GET /api/v1/mikrotik/<id>/system/health
  GET /api/v1/mikrotik/<id>/system/identity
  GET /api/v1/mikrotik/<id>/system/clock
  GET /api/v1/mikrotik/<id>/system/routerboard
  GET /api/v1/mikrotik/<id>/system/overview  ← combined dashboard call

K4 endpoints — interfaces + network:
  GET /api/v1/mikrotik/<id>/interfaces
  GET /api/v1/mikrotik/<id>/interfaces/<name>/traffic
  GET /api/v1/mikrotik/<id>/ip/addresses
  GET /api/v1/mikrotik/<id>/routes
"""
from __future__ import annotations

from flask import Blueprint, g

from ...radius.db.connection import db
from ...radius.services import mikrotik_admin_client as mac
from ...radius.services.nas_connection import resolve_connection_descriptor
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/resource",
        "mt_system_resource",
        require_api_token(mt_system_resource),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/health",
        "mt_system_health",
        require_api_token(mt_system_health),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/identity",
        "mt_system_identity",
        require_api_token(mt_system_identity),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/clock",
        "mt_system_clock",
        require_api_token(mt_system_clock),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/routerboard",
        "mt_system_routerboard",
        require_api_token(mt_system_routerboard),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/overview",
        "mt_system_overview",
        require_api_token(mt_system_overview),
        methods=["GET"],
    )
    # K4 — interfaces + network
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/interfaces",
        "mt_interfaces",
        require_api_token(mt_interfaces),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/interfaces/<string:name>/traffic",
        "mt_interface_traffic",
        require_api_token(mt_interface_traffic),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/ip/addresses",
        "mt_ip_addresses",
        require_api_token(mt_ip_addresses),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/routes",
        "mt_ip_routes",
        require_api_token(mt_ip_routes),
        methods=["GET"],
    )


# ─── helpers ─────────────────────────────────────────────────────


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _load_nas(nas_id: int) -> dict | None:
    """Fetch a `nas_devices` row including the VPN columns added in
    K1.1. Returns a dict (sqlite Row.__class__ implements mapping)
    or None when not found / wrong tenant."""
    row = db().execute(
        "SELECT * FROM nas_devices WHERE id = ? AND tenant_id = ? "
        "AND (deleted_at IS NULL OR deleted_at = '')",
        (nas_id, _tid()),
    ).fetchone()
    return dict(row) if row else None


def _envelope(result: mac.MtResult, *, router_id: int) -> dict:
    """Adds router_id + a name hint to the standard MtResult dict."""
    payload = result.to_dict()
    payload["router_id"] = router_id
    return payload


# ─── endpoints ───────────────────────────────────────────────────


def mt_system_resource(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.system_resource(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_health(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.system_health(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_identity(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.system_identity(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_clock(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.system_clock(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_routerboard(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.system_routerboard(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_overview(nas_id: int):
    """One-call dashboard backing: returns every system_* fetch in
    a single response so the K9 page makes ONE HTTP request on
    refresh.

    Each sub-call still goes through the per-operation cache, so a
    follow-up sub-page request (e.g. just `/system/health` on a
    deep-link) re-uses the warm value."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)

    sections = {
        "resource": mac.system_resource(nas),
        "health": mac.system_health(nas),
        "identity": mac.system_identity(nas),
        "clock": mac.system_clock(nas),
        "routerboard": mac.system_routerboard(nas),
    }
    descriptor = resolve_connection_descriptor(nas)
    payload = {
        "router_id": nas_id,
        "name": nas.get("name") or "",
        "connection": descriptor,
        "sections": {key: result.to_dict() for key, result in sections.items()},
        # Top-level "is the router talking to us at all?" flag —
        # the UI uses this for the big green/red header chip.
        "any_ok": any(r.ok for r in sections.values()),
        "all_ok": all(r.ok for r in sections.values()),
    }
    return ok(payload)


# ─── K4: interfaces + network endpoints ──────────────────────────


def mt_interfaces(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.interface_list(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_interface_traffic(nas_id: int, name: str):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.interface_traffic(nas, name)
    payload = _envelope(result, router_id=nas_id)
    payload["interface"] = name
    return ok(payload)


def mt_ip_addresses(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.ip_addresses(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_ip_routes(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("الراوتر غير موجود", code="not_found", status=404)
    result = mac.ip_routes(nas)
    return ok(_envelope(result, router_id=nas_id))
