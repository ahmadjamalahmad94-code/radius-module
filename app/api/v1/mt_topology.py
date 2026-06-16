"""mikrotik topology — v1 JSON API (feat/api-first-parity, group 7b).

Mirrors the topology web page (`routes/mt_topology.py`,
`/admin/radius/topology`) as JSON. Read-only — reuses the same aggregator
(`mt_topology.build_topology` + health overlay via `mt_router_overview` /
`mt_health_score`), the same secret-safe node projection, and the same
``show`` / ``health`` / ``q`` filters. No router contact (cached snapshot).
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.mt_health_score import ALL_STATES as HEALTH_STATES, score_health
from ...radius.services.mt_router_overview import build_overview
from ...radius.services.mt_topology import (
    build_topology, overlay_health, _node_to_safe_dict,
)
from ..auth import require_api_token
from ..responses import ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik/topology", "mt_topology",
                    require_api_token(topology), methods=["GET"])


def _apply_filters(routers, *, show: str, q: str | None, health: str):
    """نفس منطق فلترة صفحة الويب (routes/mt_topology._apply_filters)."""
    out = list(routers)
    if show == "offline":
        out = [r for r in out if r.status == "offline"]
    elif show == "warnings":
        out = [r for r in out if r.status in {"offline", "unknown"}]
    elif show == "vpn":
        out = [r for r in out if r.connection_mode == "vpn"]
    if health and health != "all":
        out = [r for r in out if r.health_state == health]
    if q:
        ql = q.lower()
        out = [r for r in out
               if ql in (r.label or "").lower() or ql in (r.address or "").lower()]
    return out


def _health_overlay(tenant_id: int, router_ids: list[str]) -> dict[str, dict]:
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
        out[rid] = {"state": hs.state, "score": hs.score, "signal": hs.primary_signal}
    return out


def topology():
    """GET /mikrotik/topology — الخريطة (خادم + راوترات + روابط) مع صحّة
    كل راوتر والفلاتر show/health/q (يطابق صفحة الويب)."""
    tid = _tid()
    topo = build_topology(tid)
    overlay_health(topo, _health_overlay(tid, [n.id for n in topo.routers]))

    show = (request.args.get("show") or "all").strip().lower()
    if show not in {"all", "offline", "warnings", "vpn"}:
        show = "all"
    health = (request.args.get("health") or "all").strip().lower()
    if health not in set(HEALTH_STATES) | {"all"}:
        health = "all"
    q = (request.args.get("q") or "").strip() or None

    filtered = _apply_filters(topo.routers, show=show, q=q, health=health)
    return ok({
        "server": _node_to_safe_dict(topo.server),
        "routers": [_node_to_safe_dict(n) for n in filtered],
        "links": [{"source": l.source, "target": l.target, "kind": l.kind}
                  for l in topo.links],
        "total_count": len(topo.routers),
        "filtered_count": len(filtered),
        "show": show,
        "health": health,
        "q": q or "",
        "health_states": list(HEALTH_STATES),
    })
