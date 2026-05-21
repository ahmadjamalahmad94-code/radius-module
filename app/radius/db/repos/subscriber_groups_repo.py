"""Subscriber Groups repo — مجموعات المشتركين + خدماتها (migration 027)."""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


def _row(r) -> dict:
    return {
        "id":                     r["id"],
        "tenant_id":              r["tenant_id"],
        "name":                   r["name"],
        "description":            r["description"] or "",
        "bandwidth_schedule_id":  r["bandwidth_schedule_id"],
        "default_plan_id":        r["default_plan_id"],
        "default_auto_renewal":   bool(r["default_auto_renewal"]),
        "working_days":           r["working_days"] or "",
        "created_at":             r["created_at"],
        "updated_at":             r["updated_at"],
    }


def list_groups(tenant_id: int) -> list[dict]:
    """All non-deleted groups for this tenant + member count."""
    cur = db().execute("""
        SELECT g.*,
               (SELECT COUNT(*) FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.subscriber_group_id = g.id
                   AND s.deleted_at IS NULL) AS members
        FROM subscriber_groups g
        WHERE g.tenant_id = ?
          AND g.deleted_at IS NULL
        ORDER BY g.name COLLATE NOCASE
    """, (tenant_id,))
    out: list[dict] = []
    for r in cur.fetchall():
        d = _row(r); d["members"] = r["members"]
        out.append(d)
    return out


def get(tenant_id: int, gid: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM subscriber_groups "
        "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (tenant_id, gid),
    ).fetchone()
    return _row(row) if row else None


def get_by_name(tenant_id: int, name: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM subscriber_groups "
        "WHERE tenant_id = ? AND name = ? AND deleted_at IS NULL",
        (tenant_id, name),
    ).fetchone()
    return _row(row) if row else None


def create(*, tenant_id: int, name: str, description: str = "",
           bandwidth_schedule_id: Optional[int] = None,
           default_plan_id: Optional[int] = None,
           default_auto_renewal: bool = True,
           working_days: str = "") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("group name required")
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO subscriber_groups(
                tenant_id, name, description,
                bandwidth_schedule_id, default_plan_id,
                default_auto_renewal, working_days, created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (tenant_id, name, description or "",
              bandwidth_schedule_id, default_plan_id,
              int(bool(default_auto_renewal)),
              working_days or "", now_iso()))
        return int(cur.lastrowid)


def update(tenant_id: int, gid: int, **changes) -> Optional[dict]:
    allowed = (
        "name", "description",
        "bandwidth_schedule_id", "default_plan_id",
        "default_auto_renewal", "working_days",
    )
    sets, vals = [], []
    for k, v in changes.items():
        if k not in allowed:
            continue
        if k == "default_auto_renewal":
            v = int(bool(v))
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return get(tenant_id, gid)
    sets.append("updated_at = ?"); vals.append(now_iso())
    vals += [tenant_id, gid]
    with transaction() as conn:
        conn.execute(
            f"UPDATE subscriber_groups SET {', '.join(sets)} "
            "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
            vals,
        )
    return get(tenant_id, gid)


def delete(tenant_id: int, gid: int) -> None:
    """Soft delete — also detaches members so they aren't orphaned."""
    with transaction() as conn:
        conn.execute(
            "UPDATE subscribers SET subscriber_group_id = NULL "
            "WHERE tenant_id = ? AND subscriber_group_id = ?",
            (tenant_id, gid),
        )
        conn.execute(
            "UPDATE subscriber_groups SET deleted_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (now_iso(), tenant_id, gid),
        )


def list_members(tenant_id: int, gid: int, limit: int = 500) -> list[dict]:
    cur = db().execute("""
        SELECT id, username, full_name, status, mobile
          FROM subscribers
         WHERE tenant_id = ?
           AND subscriber_group_id = ?
           AND deleted_at IS NULL
         ORDER BY username
         LIMIT ?
    """, (tenant_id, gid, limit))
    return [dict(r) for r in cur.fetchall()]
