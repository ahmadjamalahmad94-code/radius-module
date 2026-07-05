"""Share Groups repo — مجموعات مشاركة الباندويث/الكوتا."""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


def _row(r) -> dict:
    return {
        "id": r["id"], "tenant_id": r["tenant_id"], "name": r["name"],
        "description": r["description"] or "",
        "shared_quota_mb": r["shared_quota_mb"],
        "shared_speed_down_kbps": r["shared_speed_down_kbps"],
        "shared_speed_up_kbps": r["shared_speed_up_kbps"],
        "max_members": r["max_members"],
        "enabled": bool(r["enabled"]),
        "created_at": r["created_at"],
    }


def list_groups(tenant_id: int) -> list[dict]:
    cur = db().execute("""
        SELECT g.*, (SELECT COUNT(*) FROM share_group_members m WHERE m.group_id = g.id) AS members
        FROM share_groups g WHERE g.tenant_id = ? ORDER BY g.id
    """, (tenant_id,))
    out = []
    for r in cur.fetchall():
        d = _row(r); d["members"] = r["members"]; out.append(d)
    return out


def get(tenant_id: int, gid: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM share_groups WHERE tenant_id = ? AND id = ?",
        (tenant_id, gid)).fetchone()
    return _row(row) if row else None


def create(*, tenant_id: int, name: str, description: str = "",
           shared_quota_mb: int = 0, shared_speed_down_kbps: int = 0,
           shared_speed_up_kbps: int = 0, max_members: int = 0,
           enabled: bool = True) -> int:
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO share_groups(tenant_id, name, description,
                shared_quota_mb, shared_speed_down_kbps, shared_speed_up_kbps,
                max_members, enabled, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (tenant_id, name, description, shared_quota_mb,
              shared_speed_down_kbps, shared_speed_up_kbps,
              max_members, int(enabled), now_iso()))
        return cur.lastrowid


def update(tenant_id: int, gid: int, **changes) -> Optional[dict]:
    allowed = ("name", "description", "shared_quota_mb",
               "shared_speed_down_kbps", "shared_speed_up_kbps",
               "max_members", "enabled")
    sets, vals = [], []
    for k, v in changes.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(int(v) if isinstance(v, bool) else v)
    if not sets: return get(tenant_id, gid)
    vals += [tenant_id, gid]
    with transaction() as conn:
        conn.execute(f"UPDATE share_groups SET {', '.join(sets)} WHERE tenant_id = ? AND id = ?", vals)
    return get(tenant_id, gid)


def delete(tenant_id: int, gid: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM share_groups WHERE tenant_id = ? AND id = ?", (tenant_id, gid))


def list_members(group_id: int) -> list[dict]:
    cur = db().execute("""
        SELECT s.id, s.username, s.full_name, s.status
        FROM share_group_members m
        JOIN subscribers s ON s.id = m.subscriber_id
        WHERE m.group_id = ?
        ORDER BY s.username
    """, (group_id,))
    return [dict(r) for r in cur.fetchall()]


def add_member(tenant_id: int, group_id: int, subscriber_id: int) -> None:
    with transaction() as conn:
        try:
            conn.execute("""
                INSERT INTO share_group_members(tenant_id, group_id, subscriber_id, added_at)
                VALUES(?,?,?,?)
            """, (tenant_id, group_id, subscriber_id, now_iso()))
        except Exception:
            pass  # unique constraint — موجود سابقًا


def remove_member(tenant_id: int, group_id: int, subscriber_id: int) -> None:
    # SEC H8 — scope the DELETE by tenant so a caller can never strip a
    # membership out of another tenant's group by guessing gid/sid.
    with transaction() as conn:
        conn.execute(
            "DELETE FROM share_group_members "
            "WHERE tenant_id = ? AND group_id = ? AND subscriber_id = ?",
            (tenant_id, group_id, subscriber_id))
