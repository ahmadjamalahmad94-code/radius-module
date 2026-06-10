"""Persistence for RadiusPolicy rows (integration adapter CRUD).

Replaces the SQLite adapter's no-op policy methods with real, tenant-scoped
storage. A policy is identified by (tenant_id, name); `upsert_policy` inserts
when new and updates in place when the name already exists.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db
from ..helpers import json_dump, json_load, now_iso
from ...core.tenant import DEFAULT_TENANT_ID
from ...core.types import RadiusPolicy


def _row_to_policy(r) -> RadiusPolicy:
    return RadiusPolicy(
        id=int(r["id"]),
        name=r["name"] or "",
        policy_type=r["policy_type"] or "",
        tenant_id=int(r["tenant_id"]),
        params=json_load(r["params_json"], {}) or {},
        enabled=bool(r["enabled"]),
        priority=int(r["priority"] or 100),
        description=r["description"] or "",
    )


def list_policies(tenant_id: int, *, enabled: Optional[bool] = None,
                  limit: int = 200, offset: int = 0) -> list[RadiusPolicy]:
    sql = ["SELECT * FROM radius_policies WHERE tenant_id = ?"]
    vals: list = [int(tenant_id)]
    if enabled is not None:
        sql.append("AND enabled = ?")
        vals.append(1 if enabled else 0)
    sql.append("ORDER BY priority ASC, id ASC LIMIT ? OFFSET ?")
    vals.extend([int(limit), int(offset)])
    rows = db().execute(" ".join(sql), tuple(vals)).fetchall()
    return [_row_to_policy(r) for r in rows]


def get_policy(tenant_id: int, policy_id: int) -> Optional[RadiusPolicy]:
    row = db().execute(
        "SELECT * FROM radius_policies WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return _row_to_policy(row) if row else None


def get_by_name(tenant_id: int, name: str) -> Optional[RadiusPolicy]:
    row = db().execute(
        "SELECT * FROM radius_policies WHERE tenant_id = ? AND name = ?",
        (int(tenant_id), str(name)),
    ).fetchone()
    return _row_to_policy(row) if row else None


def upsert_policy(tenant_id: int, policy: RadiusPolicy) -> RadiusPolicy:
    if not (policy.name or "").strip():
        raise ValueError("policy name is required")
    now = now_iso()
    params_json = json_dump(policy.params or {})
    # Match by explicit id first, else by unique (tenant_id, name).
    existing = None
    if policy.id:
        existing = get_policy(tenant_id, int(policy.id))
    if existing is None:
        existing = get_by_name(tenant_id, policy.name)
    if existing is not None:
        db().execute(
            """
            UPDATE radius_policies
               SET name = ?, policy_type = ?, params_json = ?, enabled = ?,
                   priority = ?, description = ?, updated_at = ?
             WHERE tenant_id = ? AND id = ?
            """,
            (policy.name, policy.policy_type, params_json,
             1 if policy.enabled else 0, int(policy.priority),
             policy.description or "", now,
             int(tenant_id), int(existing.id)),
        )
        return get_policy(tenant_id, int(existing.id))  # type: ignore[return-value]
    cur = db().execute(
        """
        INSERT INTO radius_policies
            (tenant_id, name, policy_type, params_json, enabled, priority,
             description, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(tenant_id), policy.name, policy.policy_type, params_json,
         1 if policy.enabled else 0, int(policy.priority),
         policy.description or "", now),
    )
    return get_policy(tenant_id, int(cur.lastrowid))  # type: ignore[return-value]


def delete_policy(tenant_id: int, policy_id: int) -> None:
    db().execute(
        "DELETE FROM radius_policies WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(policy_id)),
    )
