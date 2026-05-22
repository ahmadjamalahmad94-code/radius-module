"""S5.2+S5.3 — Topology view + filters route.

Single page at /admin/radius/topology. Read-only — the S5.1
aggregator is pure DB, no router contact.

Filters from query string:
  ?show=offline|warnings|vpn|all
  ?q=<text>        — substring match on router name/address

The filter logic lives here (route layer) instead of in the
aggregator so the aggregator stays a clean library function.
"""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..services.mt_health_score import (
    ALL_STATES as HEALTH_STATES, score_health,
)
from ..services.mt_permissions import PERM_VIEW, requires_perm
from ..services.mt_router_overview import build_overview
from ..services.mt_topology import build_topology, overlay_health


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_topology_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/topology", "mt_topology",
        requires_perm(PERM_VIEW)(mt_topology),
        methods=["GET"],
    )


def _apply_filters(routers, *, show: str, q: str | None,
                   health: str):
    """Filter the router node list per query string. Returns a
    new list — never mutates."""
    out = list(routers)
    if show == "offline":
        out = [r for r in out if r.status == "offline"]
    elif show == "warnings":
        out = [r for r in out if r.status in {"offline", "unknown"}]
    elif show == "vpn":
        out = [r for r in out if r.connection_mode == "vpn"]
    # "all" / "" — no-op.
    if health and health != "all":
        out = [r for r in out if r.health_state == health]
    if q:
        ql = q.lower()
        out = [r for r in out
               if ql in (r.label or "").lower()
               or ql in (r.address or "").lower()]
    return out


def _build_health_overlay(
    tenant_id: int, router_ids: list[str],
) -> dict[str, dict]:
    """Compute the O2 health for each router and project into
    the small dict shape overlay_health expects.

    Pure-Python loop — N routers × small overview calls. No
    live router contact (overview reads cached snapshot only).
    """
    out: dict[str, dict] = {}
    for rid in router_ids:
        try:
            nid = int(rid)
        except (TypeError, ValueError):
            continue
        ov = build_overview(tenant_id=tenant_id, nas_id=nid)
        if ov is None:
            continue
        hs = score_health(ov)
        out[rid] = {
            "state":  hs.state,
            "score":  hs.score,
            "signal": hs.primary_signal,
        }
    return out


def mt_topology():
    tenant_id = _tid()
    topo = build_topology(tenant_id)
    healths = _build_health_overlay(
        tenant_id, [n.id for n in topo.routers])
    overlay_health(topo, healths)
    show = (request.args.get("show") or "all").strip().lower()
    if show not in {"all", "offline", "warnings", "vpn"}:
        show = "all"
    health = (request.args.get("health") or "all").strip().lower()
    if health not in set(HEALTH_STATES) | {"all"}:
        health = "all"
    q = (request.args.get("q") or "").strip() or None
    filtered = _apply_filters(
        topo.routers, show=show, q=q, health=health)
    return render_template(
        "radius/mt_topology.html",
        server=topo.server,
        routers=filtered,
        total_count=len(topo.routers),
        filtered_count=len(filtered),
        show=show, q=q or "",
        health=health,
        health_states=HEALTH_STATES,
    )
