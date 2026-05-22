"""mt_topology — S5.1 read-only topology aggregator.

Produces the data the operator's "network map" needs:
  - one server node (the HobeRadius VPS itself)
  - one node per nas_devices row
  - links from server → each router (VPN tunnel or direct)

Source of truth is `nas_devices`. We DO NOT poll any router
inside the page request — that would multiply N+1 by every
visit. When S7 lands a snapshot cache, the aggregator reads
the cached state instead (see the `from_snapshot` hook).

Secret hygiene: the data sent to the template is filtered;
api_password / api_user / secret never leave this layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db.connection import db


@dataclass
class TopologyNode:
    kind: str                   # "server" | "router"
    id: str                     # "server" or str(nas_devices.id)
    label: str                  # operator-facing
    status: str                 # "online" | "offline" | "disabled" | "unknown"
    connection_mode: str = ""   # "direct" | "vpn" | ""
    address: str = ""           # public/private address (never a secret)
    vpn_peer_address: str = ""  # tunnel endpoint when connection_mode=vpn
    risk: str = ""              # optional flag (e.g. from S6 alerts)
    # O10 — operations-view overlay. Populated by overlay_health.
    health_state: str = ""      # "" | healthy | attention | risky | offline | unknown
    health_score: int = 0       # 0..100, 0 when no overlay applied
    health_signal: str = ""     # short reason code (e.g. "no_data")
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TopologyLink:
    source: str
    target: str
    kind: str           # "vpn" | "direct"


@dataclass
class Topology:
    server: TopologyNode
    routers: list[TopologyNode]
    links: list[TopologyLink]

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": _node_to_safe_dict(self.server),
            "routers": [_node_to_safe_dict(n) for n in self.routers],
            "links": [
                {"source": l.source, "target": l.target, "kind": l.kind}
                for l in self.links
            ],
        }


# ─── Secret-safe projection ──────────────────────────────────


_ALLOWED_NODE_KEYS = (
    "kind", "id", "label", "status",
    "connection_mode", "address",
    "vpn_peer_address", "risk",
    "health_state", "health_score", "health_signal",
    "meta",
)


def _node_to_safe_dict(n: TopologyNode) -> dict[str, Any]:
    """Project a node into a dict whitelisting only the keys
    a template should see. Defence in depth in case a future
    field is added that's sensitive."""
    return {k: getattr(n, k) for k in _ALLOWED_NODE_KEYS}


# ─── Public API ──────────────────────────────────────────────


def _server_node() -> TopologyNode:
    return TopologyNode(
        kind="server", id="server",
        label="HobeRadius VPS",
        status="online",
        address="",  # never leak the public IP into JSON payloads
    )


def _router_node(row: dict) -> TopologyNode:
    enabled = bool(row.get("enabled"))
    return TopologyNode(
        kind="router",
        id=str(row.get("id") or ""),
        label=(row.get("name") or "?"),
        status="disabled" if not enabled else "unknown",
        connection_mode=(row.get("connection_mode") or "").strip().lower(),
        address=(row.get("address") or "").strip(),
        vpn_peer_address=(row.get("vpn_peer_address") or "").strip(),
        meta={"enabled": enabled},
    )


def build_topology(tenant_id: int) -> Topology:
    """Build the topology for one tenant. Pure DB read — no
    router contact. Order is stable: routers are sorted by id
    so the page layout doesn't shuffle between refreshes."""
    rows = db().execute(
        "SELECT id, name, address, enabled, connection_mode, "
        "       vpn_peer_address "
        "FROM nas_devices "
        "WHERE tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='') "
        "ORDER BY id",
        (int(tenant_id),),
    ).fetchall()

    server = _server_node()
    routers: list[TopologyNode] = []
    links: list[TopologyLink] = []
    for row in rows:
        node = _router_node(dict(row))
        routers.append(node)
        links.append(TopologyLink(
            source=server.id, target=node.id,
            kind=("vpn" if node.connection_mode == "vpn" else "direct"),
        ))
    return Topology(server=server, routers=routers, links=links)


def overlay_health(
    topo: Topology,
    healths: dict[str, dict[str, Any]] | None,
) -> Topology:
    """O10 hook — decorate router nodes with health state from
    the O2 health scorer.

    Callers pass `{router_id: {"state": str, "score": int,
    "signal": str}}`. Unknown router_ids are skipped silently.

    Pure function. Idempotent. Safe to call with `None` — it
    returns the topology unchanged so the topology view stays
    usable when health computation is unavailable.
    """
    if not healths:
        return topo
    updated = []
    for n in topo.routers:
        h = healths.get(n.id)
        if h:
            n.health_state = str(h.get("state") or "")
            try:
                n.health_score = int(h.get("score") or 0)
            except (TypeError, ValueError):
                n.health_score = 0
            n.health_signal = str(h.get("signal") or "")
        updated.append(n)
    topo.routers = updated
    return topo


def overlay_snapshots(
    topo: Topology,
    snapshots: dict[str, dict[str, Any]] | None,
) -> Topology:
    """S7 hook — when the snapshot cache lands, the aggregator
    calls this with `{router_id: snapshot_dict}` to upgrade
    `status` from 'unknown' to 'online' / 'offline' based on the
    snapshot's last_success_at + last_error.

    Pure function so it's trivially testable, and easy to skip
    when snapshots are unavailable (just pass `None`).
    """
    if not snapshots:
        return topo
    upgraded = []
    for n in topo.routers:
        snap = snapshots.get(n.id)
        if snap and n.status != "disabled":
            if snap.get("last_success_at"):
                n.status = "online"
            elif snap.get("last_error"):
                n.status = "offline"
        upgraded.append(n)
    topo.routers = upgraded
    return topo


__all__ = [
    "TopologyNode", "TopologyLink", "Topology",
    "build_topology", "overlay_snapshots", "overlay_health",
]
