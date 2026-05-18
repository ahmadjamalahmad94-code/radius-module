"""BandwidthProfile repo."""
from __future__ import annotations

from typing import Optional

from ...core.types_saas import BandwidthProfile
from ..connection import db, transaction
from ..helpers import now_iso, parse_dt


def _row(r) -> BandwidthProfile:
    return BandwidthProfile(
        id=r["id"], tenant_id=r["tenant_id"], name=r["name"],
        rate_down=r["rate_down"], rate_down_unit=r["rate_down_unit"],
        rate_up=r["rate_up"], rate_up_unit=r["rate_up_unit"],
        burst=r["burst"] or "", priority=r["priority"] or 0,
        created_at=parse_dt(r["created_at"]),
    )


def list_all(tenant_id: int) -> list[BandwidthProfile]:
    cur = db().execute(
        "SELECT * FROM bandwidth_profiles WHERE tenant_id = ? ORDER BY priority, id",
        (tenant_id,))
    return [_row(r) for r in cur.fetchall()]


def get(tenant_id: int, bw_id: int) -> Optional[BandwidthProfile]:
    row = db().execute(
        "SELECT * FROM bandwidth_profiles WHERE tenant_id = ? AND id = ?",
        (tenant_id, bw_id)).fetchone()
    return _row(row) if row else None


def upsert(b: BandwidthProfile) -> BandwidthProfile:
    now = now_iso()
    with transaction() as conn:
        if b.id is None:
            cur = conn.execute("""
                INSERT INTO bandwidth_profiles(tenant_id, name, rate_down, rate_down_unit,
                    rate_up, rate_up_unit, burst, priority, created_at)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (b.tenant_id, b.name, b.rate_down, b.rate_down_unit,
                  b.rate_up, b.rate_up_unit, b.burst, b.priority, now))
            new_id = cur.lastrowid
        else:
            conn.execute("""
                UPDATE bandwidth_profiles
                SET name=?, rate_down=?, rate_down_unit=?, rate_up=?, rate_up_unit=?, burst=?, priority=?
                WHERE tenant_id = ? AND id = ?
            """, (b.name, b.rate_down, b.rate_down_unit, b.rate_up, b.rate_up_unit,
                  b.burst, b.priority, b.tenant_id, b.id))
            new_id = b.id
    return get(b.tenant_id, new_id)


def delete(tenant_id: int, bw_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM bandwidth_profiles WHERE tenant_id = ? AND id = ?",
                     (tenant_id, bw_id))
