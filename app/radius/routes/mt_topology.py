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
from ..services.mt_permissions import PERM_VIEW, requires_perm
from ..services.mt_topology import build_topology


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_topology_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/topology", "mt_topology",
        requires_perm(PERM_VIEW)(mt_topology),
        methods=["GET"],
    )


def _apply_filters(routers, *, show: str, q: str | None):
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
    if q:
        ql = q.lower()
        out = [r for r in out
               if ql in (r.label or "").lower()
               or ql in (r.address or "").lower()]
    return out


def mt_topology():
    topo = build_topology(_tid())
    show = (request.args.get("show") or "all").strip().lower()
    if show not in {"all", "offline", "warnings", "vpn"}:
        show = "all"
    q = (request.args.get("q") or "").strip() or None
    filtered = _apply_filters(topo.routers, show=show, q=q)
    return render_template(
        "radius/mt_topology.html",
        server=topo.server,
        routers=filtered,
        total_count=len(topo.routers),
        filtered_count=len(filtered),
        show=show, q=q or "",
    )
