"""Audit log repo — DB-backed."""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import json_dump, now_iso


def record(*, tenant_id: int, actor: str, action: str, target_type: str,
           target_id: str, payload: Optional[dict] = None,
           ip_address: str = "", user_agent: str = "") -> int:
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO audit_log(tenant_id, actor, action, target_type, target_id,
                payload_json, ip_address, user_agent, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (tenant_id, actor, action, target_type, str(target_id),
              json_dump(payload or {}), ip_address, user_agent, now_iso()))
        return cur.lastrowid


def recent(tenant_id: int, *, limit: int = 200) -> list[dict]:
    cur = db().execute("""
        SELECT * FROM audit_log WHERE tenant_id = ?
        ORDER BY id DESC LIMIT ?
    """, (tenant_id, limit))
    return [dict(r) for r in cur.fetchall()]
