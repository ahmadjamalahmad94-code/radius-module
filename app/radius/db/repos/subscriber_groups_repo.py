"""Subscriber Groups repo — مجموعات المشتركين + خدماتها (migration 027)."""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso


def _g(row: Any, key: str, default=None):
    try:
        value = row[key]
        return default if value is None else value
    except (KeyError, IndexError):
        return default


def _row(r) -> dict:
    # connection_schedule was added in migration 029. Use index access via
    # keys() check so older row shapes still work.
    keys = r.keys() if hasattr(r, "keys") else []
    return {
        "id":                     r["id"],
        "tenant_id":              r["tenant_id"],
        "name":                   r["name"],
        "description":            r["description"] or "",
        "bandwidth_schedule_id":  r["bandwidth_schedule_id"],
        "default_plan_id":        r["default_plan_id"],
        "default_auto_renewal":   bool(r["default_auto_renewal"]),
        "working_days":           r["working_days"] or "",
        "connection_schedule":    (r["connection_schedule"] or "") if "connection_schedule" in keys else "",
        "created_at":             r["created_at"],
        "updated_at":             r["updated_at"],
        "members":                int(_g(r, "members", 0) or 0),
        "online_now":             int(_g(r, "online_now", 0) or 0),
        "enabled_members":        int(_g(r, "enabled_members", 0) or 0),
        "disabled_members":       int(_g(r, "disabled_members", 0) or 0),
        "expired_members":        int(_g(r, "expired_members", 0) or 0),
        "custom_speed_members":   int(_g(r, "custom_speed_members", 0) or 0),
        "temporary_speed_members": int(_g(r, "temporary_speed_members", 0) or 0),
        "total_download_bytes":   int(_g(r, "total_download_bytes", 0) or 0),
        "total_upload_bytes":     int(_g(r, "total_upload_bytes", 0) or 0),
        "session_count":          int(_g(r, "session_count", 0) or 0),
        "last_activity_at":       _g(r, "last_activity_at", "") or "",
        "default_plan_name":      _g(r, "default_plan_name", "") or "",
        "bandwidth_schedule_name": _g(r, "bandwidth_schedule_name", "") or "",
    }


def list_groups(tenant_id: int) -> list[dict]:
    """All non-deleted groups for this tenant + member count."""
    cur = db().execute("""
        SELECT g.*,
               p.name AS default_plan_name,
               bs.name AS bandwidth_schedule_name,
               (SELECT COUNT(*)
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS members,
               (SELECT COUNT(*)
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND s.status = 'enabled'
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS enabled_members,
               (SELECT COUNT(*)
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND s.status = 'disabled'
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS disabled_members,
               (SELECT COUNT(*)
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND s.status = 'expired'
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS expired_members,
               (SELECT COUNT(*)
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND s.custom_speed = 1
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS custom_speed_members,
               (SELECT COUNT(*)
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND s.temporary_speed = 1
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS temporary_speed_members,
               (SELECT COUNT(DISTINCT r.username)
                  FROM subscribers s
                  JOIN radacct r
                    ON r.tenant_id = s.tenant_id
                   AND r.username = s.username
                   AND r.acctstoptime IS NULL
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS online_now,
               (SELECT COALESCE(SUM(r.acctinputoctets), 0)
                  FROM subscribers s
                  JOIN radacct r
                    ON r.tenant_id = s.tenant_id
                   AND r.username = s.username
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS total_upload_bytes,
               (SELECT COALESCE(SUM(r.acctoutputoctets), 0)
                  FROM subscribers s
                  JOIN radacct r
                    ON r.tenant_id = s.tenant_id
                   AND r.username = s.username
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS total_download_bytes,
               (SELECT COUNT(r.radacctid)
                  FROM subscribers s
                  JOIN radacct r
                    ON r.tenant_id = s.tenant_id
                   AND r.username = s.username
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS session_count,
               (SELECT MAX(COALESCE(s.last_seen_at, s.last_login_at))
                  FROM subscribers s
                 WHERE s.tenant_id = g.tenant_id
                   AND s.deleted_at IS NULL
                   AND (s.subscriber_group_id = g.id OR s.group_name = g.name)) AS last_activity_at
        FROM subscriber_groups g
        LEFT JOIN access_plans p
          ON p.tenant_id = g.tenant_id
         AND p.id = g.default_plan_id
        LEFT JOIN bandwidth_schedules bs
          ON bs.tenant_id = g.tenant_id
         AND bs.id = g.bandwidth_schedule_id
        WHERE g.tenant_id = ?
          AND g.deleted_at IS NULL
        ORDER BY g.name COLLATE NOCASE
    """, (tenant_id,))
    return [_row(r) for r in cur.fetchall()]


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
           working_days: str = "",
           connection_schedule: str = "") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("group name required")
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO subscriber_groups(
                tenant_id, name, description,
                bandwidth_schedule_id, default_plan_id,
                default_auto_renewal, working_days,
                connection_schedule, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (tenant_id, name, description or "",
              bandwidth_schedule_id, default_plan_id,
              int(bool(default_auto_renewal)),
              working_days or "", connection_schedule or "", now_iso()))
        return int(cur.lastrowid)


def update(tenant_id: int, gid: int, **changes) -> Optional[dict]:
    allowed = (
        "name", "description",
        "bandwidth_schedule_id", "default_plan_id",
        "default_auto_renewal", "working_days",
        "connection_schedule",
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
    # NOTE: columns qualified with ``s.`` — both subscribers and
    # subscriber_groups expose ``id``, so the bare ``id``/columns made the
    # SELECT raise "ambiguous column name: id" (the member list never loaded,
    # on the web edit page too). Qualifying is the obviously-intended query.
    cur = db().execute("""
        SELECT s.id, s.username, s.full_name, s.status, s.mobile
          FROM subscribers s
          JOIN subscriber_groups g
            ON g.tenant_id = s.tenant_id
           AND g.id = ?
           AND g.deleted_at IS NULL
         WHERE s.tenant_id = ?
           AND (s.subscriber_group_id = g.id OR s.group_name = g.name)
           AND s.deleted_at IS NULL
         ORDER BY s.username
         LIMIT ?
    """, (gid, tenant_id, limit))
    return [dict(r) for r in cur.fetchall()]


def list_member_usernames(tenant_id: int, gid: int, limit: int = 10000) -> list[str]:
    rows = list_members(tenant_id, gid, limit=limit)
    return [str(r["username"]) for r in rows if r.get("username")]
