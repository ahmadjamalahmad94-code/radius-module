"""router_snapshots_repo — S7 cache for fleet-scale operations.

Pure CRUD. The refresh service (separate module) calls
`save_success` / `save_failure`; the UI calls `get_one` /
`list_for_tenant`.

Same redaction contract as jobs_repo — counters / resource
JSON go through `_redact` so a router blob with secrets in
its `comment` field can't leak.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from ..connection import db, transaction
from .jobs_repo import _redact


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for k in ("counters_json", "resource_json"):
        raw = d.get(k) or "{}"
        try:
            d[k.replace("_json", "")] = json.loads(raw)
        except (TypeError, ValueError):
            d[k.replace("_json", "")] = {}
    return d


def save_success(
    *, tenant_id: int, router_id: int,
    counters: Mapping[str, Any] | None = None,
    resource: Mapping[str, Any] | None = None,
    source: str = "live",
) -> None:
    """Refresh succeeded — store the new snapshot. UPSERT
    semantics: PRIMARY KEY on router_id."""
    now = _now()
    payload_c = json.dumps(_redact(dict(counters or {})),
                           ensure_ascii=False)
    payload_r = json.dumps(_redact(dict(resource or {})),
                           ensure_ascii=False)
    with transaction() as c:
        c.execute(
            """
            INSERT INTO router_snapshots
                (router_id, tenant_id, counters_json,
                 resource_json, last_success_at, last_error,
                 last_attempt_at, source, updated_at)
            VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(router_id) DO UPDATE SET
                counters_json   = excluded.counters_json,
                resource_json   = excluded.resource_json,
                last_success_at = excluded.last_success_at,
                last_error      = '',
                last_attempt_at = excluded.last_attempt_at,
                source          = excluded.source,
                updated_at      = excluded.updated_at
            """,
            (int(router_id), int(tenant_id),
             payload_c, payload_r, now, now, source, now),
        )


def save_failure(
    *, tenant_id: int, router_id: int, error: str,
    source: str = "live",
) -> None:
    """Refresh failed — keep the last counters/resource (the
    UI shows them as STALE) but bump last_error + attempt time.
    """
    now = _now()
    err = str(error)[:2000]
    with transaction() as c:
        # If the row doesn't exist yet, insert a minimal one so
        # the UI can render "failed, no data yet".
        c.execute(
            """
            INSERT INTO router_snapshots
                (router_id, tenant_id, counters_json,
                 resource_json, last_success_at, last_error,
                 last_attempt_at, source, updated_at)
            VALUES (?, ?, '{}', '{}', '', ?, ?, ?, ?)
            ON CONFLICT(router_id) DO UPDATE SET
                last_error      = excluded.last_error,
                last_attempt_at = excluded.last_attempt_at,
                source          = excluded.source,
                updated_at      = excluded.updated_at
            """,
            (int(router_id), int(tenant_id), err, now, source, now),
        )


def get_one(tenant_id: int, router_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM router_snapshots "
        "WHERE tenant_id=? AND router_id=?",
        (int(tenant_id), int(router_id)),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_for_tenant(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM router_snapshots WHERE tenant_id=? "
        "ORDER BY router_id",
        (int(tenant_id),),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def freshness_seconds(snap: dict, *, now: datetime | None = None) -> int:
    """Seconds since the last successful refresh. Returns a big
    number when there's never been a success — the UI can use
    that to draw a 'stale' badge."""
    if not snap:
        return 10 ** 9
    ts = snap.get("last_success_at") or ""
    if not ts:
        return 10 ** 9
    try:
        # ISO with trailing 'Z'.
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 10 ** 9
    now = now or datetime.utcnow().replace(tzinfo=when.tzinfo)
    delta = (now - when).total_seconds()
    return max(0, int(delta))


__all__ = [
    "save_success", "save_failure",
    "get_one", "list_for_tenant", "freshness_seconds",
]
