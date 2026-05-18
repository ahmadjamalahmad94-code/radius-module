"""IP Pools repo."""
from __future__ import annotations

from typing import Optional

from ...core.types_saas import IpPool
from ..connection import db, transaction
from ..helpers import now_iso, parse_dt


def _row(r) -> IpPool:
    return IpPool(
        id=r["id"], tenant_id=r["tenant_id"], pool_name=r["pool_name"],
        range_ip=r["range_ip"], local_ip=r["local_ip"] or "",
        router_id=r["router_id"], created_at=parse_dt(r["created_at"]),
    )


def list_all(tenant_id: int) -> list[IpPool]:
    cur = db().execute(
        "SELECT * FROM ip_pools WHERE tenant_id = ? ORDER BY id", (tenant_id,))
    return [_row(r) for r in cur.fetchall()]


def get(tenant_id: int, pool_id: int) -> Optional[IpPool]:
    row = db().execute(
        "SELECT * FROM ip_pools WHERE tenant_id = ? AND id = ?",
        (tenant_id, pool_id)).fetchone()
    return _row(row) if row else None


def upsert(p: IpPool) -> IpPool:
    now = now_iso()
    with transaction() as conn:
        if p.id is None:
            cur = conn.execute("""
                INSERT INTO ip_pools(tenant_id, pool_name, range_ip, local_ip, router_id, created_at)
                VALUES(?,?,?,?,?,?)
            """, (p.tenant_id, p.pool_name, p.range_ip, p.local_ip, p.router_id, now))
            new_id = cur.lastrowid
        else:
            conn.execute("""
                UPDATE ip_pools SET pool_name=?, range_ip=?, local_ip=?, router_id=?
                WHERE tenant_id = ? AND id = ?
            """, (p.pool_name, p.range_ip, p.local_ip, p.router_id, p.tenant_id, p.id))
            new_id = p.id
    return get(p.tenant_id, new_id)


def delete(tenant_id: int, pool_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM ip_pools WHERE tenant_id = ? AND id = ?",
                     (tenant_id, pool_id))
