"""jobs_repo — Phase S1 background-job persistence.

One row per work item. Lifecycle states + transitions are owned
here, not in the DB. Callers go through the public functions:

  create(...)            → returns job_id, status='queued'
  get(job_id)            → row dict (or None)
  list_recent(...)       → newest-first
  list_by_router(...)    → per-NAS history (for the per-router UI)
  mark_running(...)      → status='running', sets started_at
  update_progress(...)   → 0..100 + current_step
  mark_success(...)      → status='success', result_json redacted
  mark_failed(...)       → status='failed', error_message
  mark_cancelled(...)    → status='cancelled' (only from queued
                           or waiting; running jobs must be told
                           to cancel by the runner, not by the
                           repo)

The redact path is the safety contract from the migration: any
secret-looking key in the payload / result blobs is replaced
with "***" BEFORE it lands in storage. Tests pin this — a
regression that bypasses _redact would let a RADIUS shared
secret or a RouterOS password leak into the job row.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from ..connection import db, transaction


# ─── Lifecycle states ─────────────────────────────────────────


JOB_STATUS_QUEUED    = "queued"
JOB_STATUS_RUNNING   = "running"
JOB_STATUS_WAITING   = "waiting"
JOB_STATUS_SUCCESS   = "success"
JOB_STATUS_FAILED    = "failed"
JOB_STATUS_CANCELLED = "cancelled"

ALL_STATUSES = frozenset({
    JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_WAITING,
    JOB_STATUS_SUCCESS, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED,
})

TERMINAL_STATUSES = frozenset({
    JOB_STATUS_SUCCESS, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED,
})


# ─── Redact helper ────────────────────────────────────────────


# Keys whose values are masked before any payload / result blob
# is written. Match is case-insensitive on the *key name*. Add
# new ones liberally — false positives are cheap, leaks are
# expensive.
SECRET_KEY_FRAGMENTS = (
    "password",
    "secret",
    "private_key",
    "privatekey",
    "wg_private",
    "token",
    "bearer",
    "session",
    "cookie",
    "credential",
    "api_key",
    "apikey",
)


def _key_is_secret(key: str) -> bool:
    low = key.lower()
    return any(frag in low for frag in SECRET_KEY_FRAGMENTS)


def _redact(obj: Any) -> Any:
    """Walk dict/list trees and replace secret-keyed values with
    "***". Non-dict / non-list values pass through untouched."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _key_is_secret(str(k)):
                out[k] = "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v) for v in obj)
    return obj


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Turn a sqlite3.Row into a plain dict, parsing JSON columns
    so callers don't have to remember they're text."""
    d = dict(row)
    for k in ("payload_json", "result_json"):
        raw = d.get(k) or "{}"
        try:
            d[k.replace("_json", "")] = json.loads(raw)
        except (TypeError, ValueError):
            d[k.replace("_json", "")] = {}
    return d


# ─── CRUD ─────────────────────────────────────────────────────


def create(
    *, tenant_id: int, type: str,
    payload: Mapping[str, Any] | None = None,
    owner_admin_id: int | None = None,
    router_id: int | None = None,
) -> int:
    """Create a new job in `queued` state. Returns the new id.
    `payload` is redacted before storage."""
    if not type or not type.strip():
        raise ValueError("job type is required")
    safe = _redact(dict(payload or {}))
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO jobs
                 (tenant_id, type, status, payload_json,
                  owner_admin_id, router_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(tenant_id), type.strip(), JOB_STATUS_QUEUED,
             json.dumps(safe, ensure_ascii=False),
             owner_admin_id, router_id, now, now),
        )
        return int(cur.lastrowid)


def get(job_id: int) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT * FROM jobs WHERE id=?", (int(job_id),),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_recent(
    *, tenant_id: int, limit: int = 50,
    type_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Newest-first window into the table. `type_prefix` lets a
    UI scope to one family — pass 'mt.' to see only MikroTik
    jobs, 'print.' for print exports, etc."""
    limit = max(1, min(int(limit), 500))
    if type_prefix:
        rows = db().execute(
            "SELECT * FROM jobs "
            "WHERE tenant_id=? AND type LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (int(tenant_id), type_prefix.rstrip("%") + "%", limit),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM jobs WHERE tenant_id=? "
            "ORDER BY id DESC LIMIT ?",
            (int(tenant_id), limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_by_router(
    *, tenant_id: int, router_id: int, limit: int = 50,
) -> list[dict[str, Any]]:
    rows = db().execute(
        "SELECT * FROM jobs "
        "WHERE tenant_id=? AND router_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(router_id), int(limit)),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ─── State transitions ────────────────────────────────────────


def _set(job_id: int, **fields: Any) -> None:
    """Generic UPDATE helper; always touches updated_at."""
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with transaction() as c:
        c.execute(
            f"UPDATE jobs SET {cols} WHERE id=?",
            (*fields.values(), int(job_id)),
        )


def mark_running(job_id: int) -> None:
    """queued → running. Sets started_at."""
    _set(job_id,
         status=JOB_STATUS_RUNNING,
         started_at=_now())


def update_progress(
    job_id: int, *, percent: int, step: str = "",
) -> None:
    """Caller passes a 0..100 percentage and an optional
    Arabic step label. Out-of-range values are clamped."""
    p = max(0, min(int(percent), 100))
    fields: dict[str, Any] = {"progress": p}
    if step:
        fields["current_step"] = str(step)[:200]
    _set(job_id, **fields)


def mark_success(
    job_id: int, *, result: Mapping[str, Any] | None = None,
) -> None:
    safe = _redact(dict(result or {}))
    _set(job_id,
         status=JOB_STATUS_SUCCESS,
         progress=100,
         result_json=json.dumps(safe, ensure_ascii=False),
         finished_at=_now())


def mark_failed(
    job_id: int, *, error: str,
    result: Mapping[str, Any] | None = None,
) -> None:
    safe = _redact(dict(result or {}))
    _set(job_id,
         status=JOB_STATUS_FAILED,
         error_message=str(error)[:2000],
         result_json=json.dumps(safe, ensure_ascii=False),
         finished_at=_now())


def mark_cancelled(job_id: int) -> bool:
    """Only valid from `queued` or `waiting`. Running jobs must
    cooperate with the runner instead — calling this on a running
    job is a no-op (returns False) so a UI 'cancel' button can't
    leave the worker spinning while the row says cancelled.
    """
    row = get(job_id)
    if not row:
        return False
    if row["status"] not in {JOB_STATUS_QUEUED, JOB_STATUS_WAITING}:
        return False
    _set(job_id,
         status=JOB_STATUS_CANCELLED,
         finished_at=_now())
    return True


__all__ = [
    "JOB_STATUS_QUEUED",
    "JOB_STATUS_RUNNING",
    "JOB_STATUS_WAITING",
    "JOB_STATUS_SUCCESS",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_CANCELLED",
    "ALL_STATUSES",
    "TERMINAL_STATUSES",
    "SECRET_KEY_FRAGMENTS",
    "create",
    "get",
    "list_recent",
    "list_by_router",
    "mark_running",
    "update_progress",
    "mark_success",
    "mark_failed",
    "mark_cancelled",
]
