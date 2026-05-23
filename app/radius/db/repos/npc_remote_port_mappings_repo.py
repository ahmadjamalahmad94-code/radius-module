"""npc_remote_port_mappings_repo — port assignments for the
NPC remote-tunnel feature.

One row per (router, service). The allocator picks the next
free port in a configurable range; once assigned, the mapping
is stable for that router+service pair.

Tenant-scoped reads. No fancy soft-delete — we either have a
mapping or we don't.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..connection import db, transaction
from ..helpers import now_iso


# Service identifiers — must match the keys in
# `npc_remote_access_urls.compute_access_urls`.
SERVICE_WINBOX       = "winbox"
SERVICE_WEBFIG_HTTPS = "webfig_https"
SERVICE_WEBFIG_HTTP  = "webfig_http"
SERVICE_SSH          = "ssh"
SERVICE_API          = "api"
SERVICE_API_SSL      = "api_ssl"

ALL_SERVICES = (
    SERVICE_WINBOX, SERVICE_WEBFIG_HTTPS, SERVICE_WEBFIG_HTTP,
    SERVICE_SSH, SERVICE_API, SERVICE_API_SSL,
)


# Public port range — keep aligned with deploy/docker-compose.yml.
# 200 ports is enough for ~30 routers with all 6 services each,
# or ~50 routers with the typical 3-4 enabled.
PORT_RANGE_BASE     = 51000
PORT_RANGE_CEILING  = 51199


def _row_to_dict(r) -> dict:
    return {
        "id":                int(r["id"]),
        "tenant_id":         int(r["tenant_id"]),
        "router_id":         int(r["router_id"]),
        "service":           str(r["service"]),
        "public_port":       int(r["public_port"]),
        "upstream_address":  str(r["upstream_address"]),
        "upstream_port":     int(r["upstream_port"]),
        "enabled":           bool(r["enabled"]),
        "created_at":        str(r["created_at"]),
        "updated_at":        str(r["updated_at"]),
    }


def get(tenant_id: int, router_id: int,
        service: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_remote_port_mappings "
        "WHERE tenant_id=? AND router_id=? AND service=?",
        (int(tenant_id), int(router_id), str(service)),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_all_enabled() -> list[dict]:
    """Every enabled mapping across every tenant. Used by the
    nginx config generator — it doesn't tenant-scope because
    nginx serves the whole VPS."""
    rows = db().execute(
        "SELECT * FROM npc_remote_port_mappings "
        "WHERE enabled=1 "
        "ORDER BY public_port ASC",
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_for_router(router_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM npc_remote_port_mappings "
        "WHERE router_id=? "
        "ORDER BY service ASC",
        (int(router_id),),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def allocate_next_port(
    *,
    port_base: int = PORT_RANGE_BASE,
    port_ceiling: int = PORT_RANGE_CEILING,
) -> int:
    """Find the lowest free port in the configured range.
    Raises RuntimeError if the range is exhausted — operator
    needs to widen the range or release stale mappings."""
    used = {
        int(r["public_port"])
        for r in db().execute(
            "SELECT public_port FROM npc_remote_port_mappings"
        ).fetchall()
    }
    for p in range(int(port_base), int(port_ceiling) + 1):
        if p not in used:
            return p
    raise RuntimeError(
        f"npc_remote_port_mappings: port range "
        f"{port_base}-{port_ceiling} is exhausted "
        f"({len(used)} in use)."
    )


def ensure(
    *,
    tenant_id: int,
    router_id: int,
    service: str,
    upstream_address: str,
    upstream_port: int,
    port_base: int = PORT_RANGE_BASE,
    port_ceiling: int = PORT_RANGE_CEILING,
) -> dict:
    """Return existing mapping or create a new one with a
    freshly allocated port. If a row exists but the upstream
    address/port changed (e.g. router moved to a new WG IP)
    we update it in place — port stays stable."""
    if service not in ALL_SERVICES:
        raise ValueError(
            f"unknown remote-tunnel service: {service!r}"
        )

    existing = get(tenant_id, router_id, service)
    if existing is not None:
        # Refresh upstream if it changed; keep port stable.
        if (existing["upstream_address"] != upstream_address
            or existing["upstream_port"] != int(upstream_port)
            or not existing["enabled"]):
            now = now_iso()
            with transaction() as c:
                c.execute(
                    "UPDATE npc_remote_port_mappings "
                    "SET upstream_address=?, upstream_port=?, "
                    "    enabled=1, updated_at=? "
                    "WHERE id=?",
                    (upstream_address, int(upstream_port),
                     now, existing["id"]),
                )
            existing["upstream_address"] = upstream_address
            existing["upstream_port"] = int(upstream_port)
            existing["enabled"] = True
            existing["updated_at"] = now
        return existing

    port = allocate_next_port(
        port_base=port_base, port_ceiling=port_ceiling,
    )
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO npc_remote_port_mappings "
            "(tenant_id, router_id, service, public_port, "
            " upstream_address, upstream_port, enabled, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (int(tenant_id), int(router_id), str(service),
             int(port), str(upstream_address),
             int(upstream_port), now, now),
        )
        new_id = int(cur.lastrowid)
    return {
        "id":                new_id,
        "tenant_id":         int(tenant_id),
        "router_id":         int(router_id),
        "service":           str(service),
        "public_port":       int(port),
        "upstream_address":  str(upstream_address),
        "upstream_port":     int(upstream_port),
        "enabled":           True,
        "created_at":        now,
        "updated_at":        now,
    }


def disable_for_router(router_id: int) -> int:
    """Mark every mapping for a router as disabled. Called when
    the router is deleted or when an operator wants to close
    the relay without losing the port assignment."""
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            "UPDATE npc_remote_port_mappings "
            "SET enabled=0, updated_at=? "
            "WHERE router_id=? AND enabled=1",
            (now, int(router_id)),
        )
        return cur.rowcount


def disable(tenant_id: int, router_id: int,
            service: str) -> bool:
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            "UPDATE npc_remote_port_mappings "
            "SET enabled=0, updated_at=? "
            "WHERE tenant_id=? AND router_id=? AND service=? "
            "  AND enabled=1",
            (now, int(tenant_id), int(router_id), str(service)),
        )
        return cur.rowcount > 0


__all__ = [
    "ALL_SERVICES",
    "SERVICE_WINBOX", "SERVICE_WEBFIG_HTTPS",
    "SERVICE_WEBFIG_HTTP", "SERVICE_SSH",
    "SERVICE_API", "SERVICE_API_SSL",
    "PORT_RANGE_BASE", "PORT_RANGE_CEILING",
    "get", "list_all_enabled", "list_for_router",
    "allocate_next_port", "ensure",
    "disable_for_router", "disable",
]
