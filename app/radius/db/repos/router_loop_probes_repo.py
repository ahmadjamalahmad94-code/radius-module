"""router_loop_probes_repo — DHCP-client loop probes per router interface.

A router-side script pushes each probe's DHCP-client reading (status + leased
address + server). We keep one row per (tenant, router, interface) with the
latest reading; smart_alerts raises auto.router.loop when a probe is `bound`
(got a lease on a port that should never see DHCP → loop).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..connection import db, transaction


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def upsert_reading(*, tenant_id: int, router_id: int, interface: str,
                   status: str = "", lease_ip: str = "",
                   server_ip: str = "", enabled: bool = True) -> None:
    now = _now()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO router_loop_probes(
                tenant_id, router_id, interface, enabled, last_status,
                last_lease_ip, last_server_ip, last_reading_at,
                created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, router_id, interface) DO UPDATE SET
                enabled=excluded.enabled,
                last_status=excluded.last_status,
                last_lease_ip=excluded.last_lease_ip,
                last_server_ip=excluded.last_server_ip,
                last_reading_at=excluded.last_reading_at,
                updated_at=excluded.updated_at
            """,
            (int(tenant_id), int(router_id), str(interface)[:64],
             1 if enabled else 0, str(status or "")[:20],
             str(lease_ip or "")[:64], str(server_ip or "")[:64],
             now, now, now),
        )


def list_for_router(tenant_id: int, router_id: int) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM router_loop_probes WHERE tenant_id=? AND router_id=? ORDER BY interface",
        (int(tenant_id), int(router_id)),
    )
    return [dict(r) for r in cur.fetchall()]


def list_for_tenant(tenant_id: int) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM router_loop_probes WHERE tenant_id=? ORDER BY router_id, interface",
        (int(tenant_id),),
    )
    return [dict(r) for r in cur.fetchall()]


def routers_with_probes(tenant_id: int) -> list[int]:
    cur = db().execute(
        "SELECT DISTINCT router_id FROM router_loop_probes WHERE tenant_id=?",
        (int(tenant_id),),
    )
    return [int(r["router_id"]) for r in cur.fetchall()]
