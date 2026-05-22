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
  GET /api/v1/mikrotik/<id>/interfaces/<name>/sse  ← live SSE stream
  GET /api/v1/mikrotik/<id>/ip/addresses
  GET /api/v1/mikrotik/<id>/routes

K5 endpoints — hotspot + PPP active users:
  GET  /api/v1/mikrotik/<id>/hotspot/active
  GET  /api/v1/mikrotik/<id>/ppp/active
  POST /api/v1/mikrotik/<id>/hotspot/active/<sid>/disconnect
  POST /api/v1/mikrotik/<id>/ppp/active/<sid>/disconnect

K6 endpoints — simple queues + firewall:
  GET  /api/v1/mikrotik/<id>/queues/simple
  PUT  /api/v1/mikrotik/<id>/queues/simple/<qid>
  GET  /api/v1/mikrotik/<id>/firewall/filter
  GET  /api/v1/mikrotik/<id>/firewall/nat
  GET  /api/v1/mikrotik/<id>/firewall/address-lists
  POST /api/v1/mikrotik/<id>/firewall/address-lists
  DELETE /api/v1/mikrotik/<id>/firewall/address-lists/<eid>

K7 endpoints — logs + diagnostics:
  GET  /api/v1/mikrotik/<id>/log?topics=…&limit=…
  POST /api/v1/mikrotik/<id>/tools/ping
  POST /api/v1/mikrotik/<id>/tools/traceroute
  POST /api/v1/mikrotik/<id>/tools/dns-resolve

K8 endpoints — files + backup + downloads + destructive actions:
  GET  /api/v1/mikrotik/<id>/files
  POST /api/v1/mikrotik/<id>/system/backup/save
  GET  /api/v1/mikrotik/<id>/files/<name>/download   (501 — see K8.1b)
  POST /api/v1/mikrotik/<id>/system/reboot           (confirm=true required)
  POST /api/v1/mikrotik/<id>/system/identity/set     (confirm=true required)
"""
from __future__ import annotations

import concurrent.futures
import json
from datetime import datetime, timezone

from flask import Blueprint, Response, g, request

from ...radius.db.connection import db
from ...radius.services import mikrotik_admin_client as mac
from ...radius.services import mt_counters as counters_svc
from ...radius.services.audit import get_audit_service
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
        "/mikrotik/<int:nas_id>/interfaces/<string:name>/sse",
        "mt_interface_sse",
        require_api_token(mt_interface_sse),
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
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/neighbors",
        "mt_ip_neighbors",
        require_api_token(mt_ip_neighbors),
        methods=["GET"],
    )
    # P7 — per-router risk signals (loops / flapping / overlap)
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/health",
        "mt_router_health",
        require_api_token(mt_router_health),
        methods=["GET"],
    )
    # K5 — hotspot + PPP active users
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/hotspot/active",
        "mt_hotspot_active",
        require_api_token(mt_hotspot_active),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/ppp/active",
        "mt_ppp_active",
        require_api_token(mt_ppp_active),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/hotspot/active/<string:session_id>/disconnect",
        "mt_hotspot_disconnect",
        require_api_token(mt_hotspot_disconnect),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/ppp/active/<string:session_id>/disconnect",
        "mt_ppp_disconnect",
        require_api_token(mt_ppp_disconnect),
        methods=["POST"],
    )
    # K6 — simple queues
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/queues/simple",
        "mt_queues_simple_list",
        require_api_token(mt_queues_simple_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/queues/simple/<string:queue_id>",
        "mt_queues_simple_set",
        require_api_token(mt_queues_simple_set),
        methods=["PUT"],
    )
    # K6 — firewall (filter + nat read-only, address-lists CRUD)
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/firewall/filter",
        "mt_firewall_filter",
        require_api_token(mt_firewall_filter),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/firewall/nat",
        "mt_firewall_nat",
        require_api_token(mt_firewall_nat),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/firewall/address-lists",
        "mt_address_lists",
        require_api_token(mt_address_lists),
        methods=["GET", "POST"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/firewall/address-lists/<string:entry_id>",
        "mt_address_list_remove",
        require_api_token(mt_address_list_remove),
        methods=["DELETE"],
    )
    # K7 — log tail
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/log",
        "mt_log_tail",
        require_api_token(mt_log_tail),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/tools/ping",
        "mt_tool_ping",
        require_api_token(mt_tool_ping),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/tools/traceroute",
        "mt_tool_traceroute",
        require_api_token(mt_tool_traceroute),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/tools/dns-resolve",
        "mt_tool_dns_resolve",
        require_api_token(mt_tool_dns_resolve),
        methods=["POST"],
    )
    # K8 — backup + files + destructive system actions
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/files",
        "mt_files_list",
        require_api_token(mt_files_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/backup/save",
        "mt_system_backup_save",
        require_api_token(mt_system_backup_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/files/<string:filename>/download",
        "mt_file_download",
        require_api_token(mt_file_download),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/reboot",
        "mt_system_reboot",
        require_api_token(mt_system_reboot),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/system/identity/set",
        "mt_system_identity_set",
        require_api_token(mt_system_identity_set),
        methods=["POST"],
    )
    # O1 — Operations Center counters
    bp.add_url_rule(
        "/mikrotik/<int:nas_id>/counters",
        "mt_counters",
        require_api_token(mt_counters),
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
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.system_resource(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_health(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.system_health(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_identity(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.system_identity(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_clock(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.system_clock(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_routerboard(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.system_routerboard(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_overview(nas_id: int):
    """One-call dashboard backing: returns every system_* fetch in
    a single response so the K9 page makes ONE HTTP request on
    refresh.

    Sub-calls run in parallel (5 threads) so an offline router
    bottoms out at ~timeout_sec instead of 5×timeout_sec. Each
    sub-call still goes through the per-operation cache, so a
    follow-up sub-page request (e.g. just `/system/health`) re-uses
    the warm value.
    """
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)

    fetchers = {
        "resource": mac.system_resource,
        "health": mac.system_health,
        "identity": mac.system_identity,
        "clock": mac.system_clock,
        "routerboard": mac.system_routerboard,
    }
    sections: dict[str, mac.MtResult] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(fetchers),
        thread_name_prefix="mt-ov",
    ) as ex:
        future_to_key = {
            ex.submit(fn, nas): key for key, fn in fetchers.items()
        }
        for fut in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[fut]
            try:
                sections[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                # Defensive — `_safe_dial` already swallows MT errors
                # into the MtResult envelope; this catch is for the
                # truly-unexpected case so one bad sub-call doesn't
                # 500 the whole overview.
                sections[key] = mac.MtResult(
                    ok=False, error=f"unhandled: {exc}",
                )
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
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.interface_list(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_interface_traffic(nas_id: int, name: str):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.interface_traffic(nas, name)
    payload = _envelope(result, router_id=nas_id)
    payload["interface"] = name
    return ok(payload)


def mt_ip_addresses(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.ip_addresses(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_ip_neighbors(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.ip_neighbors(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_router_health(nas_id: int):
    """P7 — aggregate risk-signal scan for one router.

    Reuses the cached K4 readers so a 30s UI poll never costs more
    than the existing interfaces/addresses cache TTL would have."""
    from ...radius.services import mt_health
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    report = mt_health.scan_router(nas)
    report["router_id"] = nas_id
    return ok(report)


def mt_ip_routes(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.ip_routes(nas)
    return ok(_envelope(result, router_id=nas_id))


# ─── K5: hotspot + PPP active users endpoints ────────────────────


def mt_hotspot_active(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.hotspot_active(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_ppp_active(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.ppp_active(nas)
    return ok(_envelope(result, router_id=nas_id))


def _audit_mutation(
    *, nas_id: int, action: str, target_id: str, result: mac.MtResult,
    extra: dict | None = None,
) -> None:
    """Common audit hook for K5+ MT mutations. Survives audit-table
    failures (the service swallows internally) so the operator
    still gets a useful HTTP response if the audit DB is down.

    `extra` lets K8 destructive endpoints attach a `reason` field
    without overloading `target_id`.
    """
    actor = str(getattr(g, "admin_id", None) or "api")
    payload = {
        "session_id": target_id,
        "ok": result.ok,
        "error": result.error,
        "took_ms": result.took_ms,
        "dialed_address": result.dialed_address,
        "mode": result.mode,
    }
    if extra:
        payload.update(extra)
    get_audit_service().record(
        actor=actor,
        action=action,
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        payload=payload,
    )


def mt_hotspot_disconnect(nas_id: int, session_id: str):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.disconnect_hotspot_session(nas, session_id)
    _audit_mutation(
        nas_id=nas_id, action="mt.hotspot.disconnect",
        target_id=session_id, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["session_id"] = session_id
    return ok(payload)


def mt_ppp_disconnect(nas_id: int, session_id: str):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.disconnect_ppp_session(nas, session_id)
    _audit_mutation(
        nas_id=nas_id, action="mt.ppp.disconnect",
        target_id=session_id, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["session_id"] = session_id
    return ok(payload)


# ─── K6: simple queues endpoints ─────────────────────────────────


def mt_queues_simple_list(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.queue_simple_list(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_queues_simple_set(nas_id: int, queue_id: str):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("bad_request", "الجسم يجب أن يكون JSON object", status=400)
    result = mac.queue_simple_set(nas, queue_id, body)
    _audit_mutation(
        nas_id=nas_id, action="mt.queue.simple.set",
        target_id=queue_id, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["queue_id"] = queue_id
    return ok(payload)


# ─── K6: firewall endpoints ──────────────────────────────────────


def mt_firewall_filter(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    return ok(_envelope(mac.firewall_filter(nas), router_id=nas_id))


def mt_firewall_nat(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    return ok(_envelope(mac.firewall_nat(nas), router_id=nas_id))


def mt_address_lists(nas_id: int):
    """GET → read every address-list entry; POST → add a new one.

    The route handles two methods so the URL stays
    `/firewall/address-lists` for both — RESTful and matches the K9
    UI's form action.
    """
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)

    if request.method == "GET":
        return ok(_envelope(mac.address_list_list(nas), router_id=nas_id))

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("bad_request", "الجسم يجب أن يكون JSON object", status=400)
    result = mac.address_list_add(
        nas,
        list_name=str(body.get("list") or ""),
        address=str(body.get("address") or ""),
        comment=str(body.get("comment") or ""),
        timeout=str(body.get("timeout") or ""),
    )
    _audit_mutation(
        nas_id=nas_id, action="mt.firewall.address_list.add",
        target_id=f"{body.get('list')}::{body.get('address')}",
        result=result,
    )
    return ok(_envelope(result, router_id=nas_id))


def mt_address_list_remove(nas_id: int, entry_id: str):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = mac.address_list_remove(nas, entry_id)
    _audit_mutation(
        nas_id=nas_id, action="mt.firewall.address_list.remove",
        target_id=entry_id, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["entry_id"] = entry_id
    return ok(payload)


# ─── K7: log tail ────────────────────────────────────────────────


def _parse_limit(raw: str | None, *, default: int = 100, cap: int = 1000) -> int:
    try:
        n = int(raw) if raw else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


def mt_log_tail(nas_id: int):
    """`?topics=foo,bar&limit=200`. Topics are matched substring-wise
    against each row's `topics` field (RouterOS stores topics as a
    comma-separated string)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    topics_raw = request.args.get("topics") or ""
    topics = [t for t in topics_raw.split(",") if t.strip()]
    limit = _parse_limit(request.args.get("limit"))
    result = mac.log_tail(nas, topics=topics, limit=limit)
    payload = _envelope(result, router_id=nas_id)
    payload["topics"] = topics
    payload["limit"] = limit
    return ok(payload)


# ─── K7.2: diagnostic tools ──────────────────────────────────────


def _tool_body() -> dict:
    """JSON body shared by every /tools/* endpoint. Always returns a
    dict so the handler can index it without guarding for None."""
    body = request.get_json(silent=True) or {}
    return body if isinstance(body, dict) else {}


def mt_tool_ping(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = _tool_body()
    target = str(body.get("target") or "")
    count = int(body.get("count") or 4)
    result = mac.tool_ping(nas, target=target, count=count)
    _audit_mutation(
        nas_id=nas_id, action="mt.tools.ping",
        target_id=target, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["target"] = target
    payload["count"] = count
    return ok(payload)


def mt_tool_traceroute(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = _tool_body()
    target = str(body.get("target") or "")
    count = int(body.get("count") or 1)
    result = mac.tool_traceroute(nas, target=target, count=count)
    _audit_mutation(
        nas_id=nas_id, action="mt.tools.traceroute",
        target_id=target, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["target"] = target
    payload["count"] = count
    return ok(payload)


def mt_tool_dns_resolve(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = _tool_body()
    name = str(body.get("name") or "")
    server = str(body.get("server") or "")
    result = mac.tool_dns_resolve(nas, name=name, server=server)
    _audit_mutation(
        nas_id=nas_id, action="mt.tools.dns_resolve",
        target_id=name, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["name"] = name
    if server:
        payload["server"] = server
    return ok(payload)


# ─── K8: files + backup + destructive actions ────────────────────


def _default_backup_name() -> str:
    """`backup-YYYYMMDD-HHMMSS` in UTC. Matches the
    `_BACKUP_NAME_RE` pattern so it never gets rejected by the
    sanitizer."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"backup-{stamp}"


def _require_confirm(body: dict):
    """Return a (response, status) tuple to early-return when
    `confirm` is missing/false, else None. 409 (Conflict) is the
    common 'precondition not met' shape this admin already uses
    for similar protective gates."""
    if body.get("confirm") is not True:
        return fail(
            "confirm_required",
            'هذه العملية حسّاسة — مرّر "confirm": true في الجسم',
            status=409,
        )
    return None


def mt_files_list(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    return ok(_envelope(mac.file_list(nas), router_id=nas_id))


def mt_system_backup_save(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("bad_request", "الجسم يجب أن يكون JSON object", status=400)
    name = str(body.get("name") or "").strip() or _default_backup_name()
    result = mac.backup_save(nas, name=name)
    _audit_mutation(
        nas_id=nas_id, action="mt.system.backup.save",
        target_id=name, result=result,
    )
    payload = _envelope(result, router_id=nas_id)
    payload["backup_name"] = name
    return ok(payload)


def mt_file_download(nas_id: int, filename: str):
    """K8.1b — honest unsupported response.

    The MikroTik wire client doesn't stream binary file contents
    yet. Until a real helper lands we return a 501 envelope so
    callers can detect the gap programmatically and the UI can
    show a clear "not supported" notice rather than a broken
    progress bar. The filename is still sanitized here so a future
    real implementation inherits the security check unchanged.
    """
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    safe_name = (filename or "").strip()
    if (
        not safe_name
        or "/" in safe_name
        or "\\" in safe_name
        or ".." in safe_name
    ):
        return fail(
            "invalid_filename",
            "اسم الملف غير صالح",
            status=400,
        )
    try:
        # Once the helper streams real bytes this branch is replaced
        # with a Flask `Response(generator, mimetype="application/
        # octet-stream", headers={...})`.
        mac.file_download_stream(nas, safe_name)
    except mac.FileDownloadNotSupported as exc:
        return fail(
            "not_supported",
            str(exc),
            status=501,
            details={"filename": safe_name, "router_id": nas_id},
        )
    return fail(  # pragma: no cover
        "not_supported", "تنزيل الملفات غير مدعوم", status=501,
    )


def mt_system_reboot(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("bad_request", "الجسم يجب أن يكون JSON object", status=400)
    guard = _require_confirm(body)
    if guard is not None:
        return guard
    result = mac.system_reboot(nas)
    _audit_mutation(
        nas_id=nas_id, action="mt.system.reboot",
        target_id=str(nas_id),
        result=result,
        extra={"reason": str(body.get("reason") or "")},
    )
    return ok(_envelope(result, router_id=nas_id))


# ─── O1: counters ────────────────────────────────────────────────


def mt_counters(nas_id: int):
    """Aggregated headline numbers for the Operations Center
    row: hotspot+ppp active counts, byte totals, last-seen.

    Returns the standard MtResult envelope (cached + dialed +
    mode all inherited from the underlying K3 fetchers). UI
    polls this every 10s per visible router.
    """
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    result = counters_svc.counters_for_nas(nas)
    return ok(_envelope(result, router_id=nas_id))


def mt_system_identity_set(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("bad_request", "الجسم يجب أن يكون JSON object", status=400)
    guard = _require_confirm(body)
    if guard is not None:
        return guard
    name = str(body.get("name") or "")
    result = mac.system_identity_set(nas, name=name)
    _audit_mutation(
        nas_id=nas_id, action="mt.system.identity.set",
        target_id=name, result=result,
        extra={"reason": str(body.get("reason") or "")},
    )
    payload = _envelope(result, router_id=nas_id)
    payload["new_name"] = name
    return ok(payload)




def _format_sse(payload: dict) -> str:
    """Serialise one event for an SSE stream. `ensure_ascii=False`
    keeps the Arabic error text readable in browser devtools."""
    body = json.dumps(payload, ensure_ascii=False)
    return f"data: {body}\n\n"


def mt_interface_sse(nas_id: int, name: str):
    """Server-Sent Events stream — pushes one `MtResult.to_dict()`
    every `SSE_DEFAULT_PERIOD_SEC` seconds for up to
    `SSE_DEFAULT_MAX_SAMPLES` samples (≈ 5 minutes), then closes so
    the browser's EventSource reconnects with a fresh DB lookup.

    The actual sample loop lives in
    `mikrotik_admin_client.stream_interface_samples`, which is
    cache-bypassing so the UI sees live values.
    """
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)

    def gen():
        for sample in mac.stream_interface_samples(nas, name):
            payload = sample.to_dict()
            payload["router_id"] = nas_id
            payload["interface"] = name
            yield _format_sse(payload)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            # Tell every middlebox not to buffer the stream — chunks
            # need to reach the browser the instant we yield them.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
