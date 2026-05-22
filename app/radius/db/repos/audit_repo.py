"""Audit log repo — DB-backed.

S2.1 extended the audit_log schema with severity / result_status
/ router_id / error_message / before_json / after_json. The
`record` function accepts the new fields as keyword-only with
safe defaults so every existing caller stays working without
edits — they just don't populate the new columns until they're
upgraded.

Redaction of secret-keyed values in `payload`, `before`, `after`
goes through the same helper as `jobs_repo` so the two paths
can't drift.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import json_dump, now_iso
from .jobs_repo import _redact  # shared redaction helper


_VALID_SEVERITIES = frozenset({"info", "warning", "critical"})


def record(
    *, tenant_id: int, actor: str, action: str,
    target_type: str, target_id: str,
    payload: Optional[dict] = None,
    ip_address: str = "", user_agent: str = "",
    severity: str = "info",
    result_status: str = "",
    router_id: Optional[int] = None,
    error_message: str = "",
    before: Optional[dict] = None,
    after: Optional[dict] = None,
) -> int:
    """Insert an audit row. Every dict-shaped field goes through
    `_redact` so a leaked password/secret in a payload can never
    persist past the boundary."""
    sev = severity if severity in _VALID_SEVERITIES else "info"
    safe_payload = _redact(payload or {})
    safe_before  = _redact(before  or {})
    safe_after   = _redact(after   or {})
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_log(
                tenant_id, actor, action, target_type, target_id,
                payload_json, ip_address, user_agent, created_at,
                severity, result_status, router_id, error_message,
                before_json, after_json
            )
            VALUES (?,?,?,?,?, ?,?,?,?,  ?,?,?,?,  ?,?)
            """,
            (
                tenant_id, actor, action, target_type, str(target_id),
                json_dump(safe_payload), ip_address, user_agent, now_iso(),
                sev, (result_status or "")[:32],
                int(router_id) if router_id is not None else None,
                (error_message or "")[:2000],
                json_dump(safe_before), json_dump(safe_after),
            ),
        )
        return cur.lastrowid


def recent(
    tenant_id: int, *,
    limit: int = 200,
    router_id: Optional[int] = None,
    action: Optional[str] = None,
    severity: Optional[str] = None,
    result_status: Optional[str] = None,
    search: Optional[str] = None,
) -> list[dict]:
    """S2.2-ready listing — filterable by the indexed columns
    (router_id, severity) and by exact action match. The search
    string runs a LIKE over target_id + actor + action so the
    UI's free-text field has somewhere to land. All optional;
    existing callers passing nothing extra get the previous
    newest-first behavior."""
    sql = ["SELECT * FROM audit_log WHERE tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if router_id is not None:
        sql.append("AND router_id = ?")
        params.append(int(router_id))
    if action:
        sql.append("AND action = ?")
        params.append(str(action))
    if severity:
        sql.append("AND severity = ?")
        params.append(str(severity))
    if result_status:
        sql.append("AND result_status = ?")
        params.append(str(result_status))
    if search:
        sql.append(
            "AND (target_id LIKE ? OR actor LIKE ? OR action LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    sql.append("ORDER BY id DESC LIMIT ?")
    params.append(int(limit))
    cur = db().execute(" ".join(sql), tuple(params))
    return [dict(r) for r in cur.fetchall()]


def get_by_id(tenant_id: int, audit_id: int) -> Optional[dict]:
    """S2.2 — detail-page lookup."""
    row = db().execute(
        "SELECT * FROM audit_log WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(audit_id)),
    ).fetchone()
    return dict(row) if row else None
