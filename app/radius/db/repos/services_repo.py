"""Services (hardware/معدّات) repo."""
from __future__ import annotations

from typing import Optional

from ...core.types_saas import Service
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


def _row(r) -> Service:
    return Service(
        id=r["id"], tenant_id=r["tenant_id"], subscriber_id=r["subscriber_id"],
        name=r["name"], serial=r["serial"] or "", mac=r["mac"] or "",
        type=r["type"], rent_per_month=r["rent_per_month"] or 0.0,
        status=r["status"], given_at=parse_dt(r["given_at"]),
        returned_at=parse_dt(r["returned_at"]), notes=r["notes"] or "",
        created_at=parse_dt(r["created_at"]),
    )


def list_all(tenant_id: int, *, status: Optional[str] = None,
             subscriber_id: Optional[int] = None,
             limit: int = 200, offset: int = 0) -> list[Service]:
    sql = "SELECT * FROM services WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"; vals.append(status)
    if subscriber_id is not None:
        sql += " AND subscriber_id = ?"; vals.append(subscriber_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    return [_row(r) for r in db().execute(sql, vals).fetchall()]


def get(tenant_id: int, sid: int) -> Optional[Service]:
    row = db().execute(
        "SELECT * FROM services WHERE tenant_id = ? AND id = ?",
        (tenant_id, sid)).fetchone()
    return _row(row) if row else None


def upsert(s: Service) -> Service:
    now = now_iso()
    with transaction() as conn:
        if s.id is None:
            cur = conn.execute("""
                INSERT INTO services(tenant_id, subscriber_id, name, serial, mac, type,
                    rent_per_month, status, given_at, returned_at, notes, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (s.tenant_id, s.subscriber_id, s.name, s.serial, s.mac, s.type,
                  s.rent_per_month, s.status, dt_to_iso(s.given_at),
                  dt_to_iso(s.returned_at), s.notes, now))
            new_id = cur.lastrowid
        else:
            conn.execute("""
                UPDATE services SET subscriber_id=?, name=?, serial=?, mac=?, type=?,
                    rent_per_month=?, status=?, given_at=?, returned_at=?, notes=?
                WHERE tenant_id = ? AND id = ?
            """, (s.subscriber_id, s.name, s.serial, s.mac, s.type,
                  s.rent_per_month, s.status, dt_to_iso(s.given_at),
                  dt_to_iso(s.returned_at), s.notes, s.tenant_id, s.id))
            new_id = s.id
    return get(s.tenant_id, new_id)


def delete(tenant_id: int, sid: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM services WHERE tenant_id = ? AND id = ?",
                     (tenant_id, sid))
