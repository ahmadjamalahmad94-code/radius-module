"""vps_exit_nodes_repo — VX2 VPS endpoint metadata.

A VPS exit node is the "other end" of a WireGuard tunnel that
selected destinations exit through. This table stores ONLY
metadata that the operator entered or the platform learned:

    name, public_ip, wireguard_interface_name,
    wireguard_gateway_ip, tunnel_cidr, enabled,
    last_health_status, last_handshake_at

**No private keys, ever.** WireGuard private keys must stay
outside the platform DB. The MikroTik-side key is installed
on the router; the VPS-side key lives in the VPS config. The
platform only needs the public addresses to generate scripts
and surface health.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..connection import db, transaction


# Allowed health labels — strings the UI maps to chips.
HEALTH_OK       = "ok"
HEALTH_DEGRADED = "degraded"
HEALTH_DOWN     = "down"
HEALTH_UNKNOWN  = ""

ALLOWED_HEALTH = frozenset({
    HEALTH_OK, HEALTH_DEGRADED, HEALTH_DOWN, HEALTH_UNKNOWN,
})


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def create(
    *, tenant_id: int, name: str,
    public_ip: str = "",
    wireguard_interface_name: str = "",
    wireguard_gateway_ip: str = "",
    tunnel_cidr: str = "",
    enabled: bool = False,
) -> int:
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO vps_exit_nodes
                (tenant_id, name, public_ip,
                 wireguard_interface_name, wireguard_gateway_ip,
                 tunnel_cidr, enabled,
                 last_health_status, last_handshake_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
            """,
            (
                int(tenant_id), name[:120],
                (public_ip or "")[:64],
                (wireguard_interface_name or "")[:64],
                (wireguard_gateway_ip or "")[:64],
                (tunnel_cidr or "")[:64],
                1 if enabled else 0, now, now,
            ),
        )
        return int(cur.lastrowid)


def get_by_id(tenant_id: int, node_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM vps_exit_nodes "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(node_id)),
    ).fetchone()
    return dict(row) if row else None


def get_by_name(tenant_id: int, name: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM vps_exit_nodes "
        "WHERE tenant_id=? AND name=?",
        (int(tenant_id), str(name)),
    ).fetchone()
    return dict(row) if row else None


def list_for_tenant(
    tenant_id: int, *, only_enabled: bool = False,
) -> list[dict]:
    sql = ["SELECT * FROM vps_exit_nodes WHERE tenant_id=?"]
    params: list[Any] = [int(tenant_id)]
    if only_enabled:
        sql.append("AND enabled=1")
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def update(
    tenant_id: int, node_id: int, **changes,
) -> Optional[dict]:
    """Selective update — only known columns are written.

    Refuses to write any column not in the allow-list (defence
    against future-key typos like `private_key`).
    """
    allowed = {
        "name", "public_ip", "wireguard_interface_name",
        "wireguard_gateway_ip", "tunnel_cidr", "enabled",
    }
    payload: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in allowed:
            continue
        if k == "enabled":
            payload[k] = 1 if v else 0
        else:
            payload[k] = (str(v) if v is not None else "")[:120]
    if not payload:
        return get_by_id(tenant_id, node_id)
    fields = ", ".join(f"{k}=?" for k in payload.keys())
    params: list[Any] = list(payload.values())
    params.extend([_now(), int(tenant_id), int(node_id)])
    with transaction() as c:
        c.execute(
            f"UPDATE vps_exit_nodes SET {fields}, updated_at=? "
            "WHERE tenant_id=? AND id=?",
            tuple(params),
        )
    return get_by_id(tenant_id, node_id)


def set_health(
    tenant_id: int, node_id: int, *,
    status: str = HEALTH_UNKNOWN,
    last_handshake_at: Optional[str] = None,
) -> bool:
    """Record observed health from a probe — separate from the
    operator-edited columns so a worker can write here without
    touching configuration."""
    if status not in ALLOWED_HEALTH:
        status = HEALTH_UNKNOWN
    handshake = last_handshake_at if last_handshake_at is not None else ""
    with transaction() as c:
        cur = c.execute(
            "UPDATE vps_exit_nodes "
            "SET last_health_status=?, last_handshake_at=?, "
            "    updated_at=? "
            "WHERE tenant_id=? AND id=?",
            (status, handshake, _now(),
             int(tenant_id), int(node_id)),
        )
        return cur.rowcount > 0


def delete(tenant_id: int, node_id: int) -> bool:
    """Hard delete. Caller is responsible for cascading any
    policies that referenced this node (the UI must refuse
    deletion of a node still in use)."""
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM vps_exit_nodes "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(node_id)),
        )
        return cur.rowcount > 0


__all__ = [
    "HEALTH_OK", "HEALTH_DEGRADED", "HEALTH_DOWN",
    "HEALTH_UNKNOWN", "ALLOWED_HEALTH",
    "create", "get_by_id", "get_by_name",
    "list_for_tenant", "update", "set_health", "delete",
]
