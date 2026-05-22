"""alerts_repo — S6 smart-alerts persistence.

API:
  open(rule, dedup_key, ...)  → INSERT or bump last_seen
  resolve(dedup_key)          → status='resolved' + resolved_at
  list_open / list_resolved
  list_by_router

Dedup contract: callers pass `dedup_key` built from (rule +
router_id + signal). Repeat detections of the same condition
UPDATE the existing row, never insert a duplicate.

Same redaction helper as jobs_repo runs over the `evidence`
blob before storage — alerts can carry router state, so a
nested password value gets "***" before it ever hits disk.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, Optional

from ..connection import db, transaction
from .jobs_repo import _redact


ALERT_STATUS_OPEN     = "open"
ALERT_STATUS_RESOLVED = "resolved"
ALERT_STATUS_SILENCED = "silenced"

_VALID_STATUSES = {ALERT_STATUS_OPEN, ALERT_STATUS_RESOLVED,
                   ALERT_STATUS_SILENCED}
_VALID_SEVERITIES = {"info", "warning", "critical"}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    try:
        d["evidence"] = json.loads(d.get("evidence_json") or "{}")
    except (TypeError, ValueError):
        d["evidence"] = {}
    return d


def open(
    *, tenant_id: int, rule: str, dedup_key: str,
    title_ar: str,
    router_id: int | None = None,
    severity: str = "info",
    explanation_ar: str = "",
    recommended_action_ar: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> int:
    """Open or refresh an alert. Returns the row id.

    Behavior:
      - First call → INSERT with first_seen=last_seen=now.
      - Repeat call with same (tenant_id, dedup_key) → UPDATE:
        bumps last_seen, refreshes severity/title/evidence, and
        revives `status='open'` if it had been resolved
        previously.
    """
    if severity not in _VALID_SEVERITIES:
        severity = "info"
    safe_evidence = _redact(dict(evidence or {}))
    now = _now()
    with transaction() as c:
        c.execute(
            """
            INSERT INTO alerts
                (tenant_id, router_id, rule, severity, title_ar,
                 explanation_ar, recommended_action_ar,
                 evidence_json, dedup_key, status,
                 first_seen, last_seen)
            VALUES (?,?,?,?,?, ?,?, ?,?, 'open', ?, ?)
            ON CONFLICT(tenant_id, dedup_key) DO UPDATE SET
                severity = excluded.severity,
                title_ar = excluded.title_ar,
                explanation_ar = excluded.explanation_ar,
                recommended_action_ar = excluded.recommended_action_ar,
                evidence_json = excluded.evidence_json,
                status = 'open',
                last_seen = excluded.last_seen,
                resolved_at = ''
            """,
            (
                int(tenant_id),
                int(router_id) if router_id is not None else None,
                rule, severity, title_ar,
                explanation_ar, recommended_action_ar,
                json.dumps(safe_evidence, ensure_ascii=False),
                dedup_key,
                now, now,
            ),
        )
        # SQLite's INSERT...ON CONFLICT DO UPDATE returns the
        # lastrowid of either the new insert or 0 — fetch by
        # dedup_key to be explicit.
        row = db().execute(
            "SELECT id FROM alerts "
            "WHERE tenant_id=? AND dedup_key=?",
            (int(tenant_id), dedup_key),
        ).fetchone()
        return int(row["id"]) if row else 0


def resolve(tenant_id: int, dedup_key: str) -> bool:
    """Mark an alert resolved. Returns True if a row was
    updated. No-op (False) when the alert was already
    resolved or doesn't exist."""
    now = _now()
    with transaction() as c:
        cur = c.execute(
            "UPDATE alerts SET status='resolved', resolved_at=? "
            "WHERE tenant_id=? AND dedup_key=? AND status='open'",
            (now, int(tenant_id), dedup_key),
        )
        return cur.rowcount > 0


def get_by_id(tenant_id: int, alert_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM alerts WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(alert_id)),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_open(
    tenant_id: int, *,
    router_id: int | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[dict]:
    return _list(tenant_id, status="open",
                 router_id=router_id, severity=severity,
                 limit=limit)


def list_resolved(
    tenant_id: int, *,
    router_id: int | None = None,
    limit: int = 200,
) -> list[dict]:
    return _list(tenant_id, status="resolved",
                 router_id=router_id, limit=limit)


def list_by_router(
    tenant_id: int, router_id: int,
    *, limit: int = 200,
) -> list[dict]:
    return _list(tenant_id, router_id=router_id, limit=limit)


def _list(
    tenant_id: int, *,
    status: str | None = None,
    router_id: int | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = ["SELECT * FROM alerts WHERE tenant_id=?"]
    params: list[Any] = [int(tenant_id)]
    if status:
        sql.append("AND status=?")
        params.append(status)
    if router_id is not None:
        sql.append("AND router_id=?")
        params.append(int(router_id))
    if severity:
        sql.append("AND severity=?")
        params.append(severity)
    sql.append("ORDER BY last_seen DESC LIMIT ?")
    params.append(int(limit))
    cur = db().execute(" ".join(sql), tuple(params))
    return [_row_to_dict(r) for r in cur.fetchall()]


__all__ = [
    "ALERT_STATUS_OPEN",
    "ALERT_STATUS_RESOLVED",
    "ALERT_STATUS_SILENCED",
    "open", "resolve", "get_by_id",
    "list_open", "list_resolved", "list_by_router",
]
