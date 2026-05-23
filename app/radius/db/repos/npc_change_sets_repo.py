"""npc_change_sets_repo — one execution attempt per row.

Sibling of `npc_deployments_repo` (per-policy lifecycle) but
finer-grained: every apply or rollback request lands here
with its full envelope (preview hash, snapshot id, who, when,
mode, confirmations, per-router results).

Phase 4 of the safe-execution roadmap. Pure data layer; the
apply service in `npc_apply_service.py` orchestrates over
this repo + the executor adapter.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Optional

from ..connection import db, transaction


# ─── Action types ───────────────────────────────────────────


ACTION_APPLY    = "apply"
ACTION_ROLLBACK = "rollback"

ALLOWED_ACTIONS = frozenset({ACTION_APPLY, ACTION_ROLLBACK})


# ─── Aggregate status enum ──────────────────────────────────


STATUS_PLANNED                = "planned"
STATUS_RUNNING                = "running"
STATUS_SUCCEEDED              = "succeeded"
STATUS_FAILED                 = "failed"
STATUS_PARTIALLY_SUCCEEDED    = "partially_succeeded"
STATUS_ROLLED_BACK            = "rolled_back"
STATUS_ROLLBACK_PENDING       = "rollback_pending"
STATUS_ROLLBACK_RUNNING       = "rollback_running"
STATUS_ROLLBACK_FAILED        = "rollback_failed"
STATUS_PARTIALLY_ROLLED_BACK  = "partially_rolled_back"

ALLOWED_STATUSES = frozenset({
    STATUS_PLANNED, STATUS_RUNNING,
    STATUS_SUCCEEDED, STATUS_FAILED,
    STATUS_PARTIALLY_SUCCEEDED,
    STATUS_ROLLED_BACK,
    STATUS_ROLLBACK_PENDING, STATUS_ROLLBACK_RUNNING,
    STATUS_ROLLBACK_FAILED, STATUS_PARTIALLY_ROLLED_BACK,
})


# ─── Per-router target status enum ──────────────────────────


TARGET_STATUS_PENDING     = "pending"
TARGET_STATUS_RUNNING     = "running"
TARGET_STATUS_SUCCEEDED   = "succeeded"
TARGET_STATUS_FAILED      = "failed"
TARGET_STATUS_SKIPPED     = "skipped"
TARGET_STATUS_ROLLED_BACK = "rolled_back"

ALLOWED_TARGET_STATUSES = frozenset({
    TARGET_STATUS_PENDING, TARGET_STATUS_RUNNING,
    TARGET_STATUS_SUCCEEDED, TARGET_STATUS_FAILED,
    TARGET_STATUS_SKIPPED, TARGET_STATUS_ROLLED_BACK,
})


# ─── Execution mode enum ────────────────────────────────────


MODE_CANARY   = "canary"
MODE_STAGED   = "staged"
MODE_FULL     = "full"
MODE_ROLLBACK = "rollback"

ALLOWED_MODES = frozenset({
    MODE_CANARY, MODE_STAGED, MODE_FULL, MODE_ROLLBACK,
})


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ─── Change set CRUD ────────────────────────────────────────


def create(
    *, tenant_id: int, service: str, policy_id: int,
    action_type: str,
    parent_change_set_id: Optional[int] = None,
    execution_mode: str = MODE_FULL,
    preview_hash: str = "",
    health_score: int = 0,
    health_grade: str = "",
    risk_level: str = "",
    snapshot_id: Optional[int] = None,
    requested_router_ids: Iterable[int] = (),
    confirmations: Iterable[str] = (),
    dry_run: bool = False,
    created_by: str = "",
    notes: str = "",
) -> int:
    if action_type not in ALLOWED_ACTIONS:
        raise ValueError(
            f"invalid action_type: {action_type!r}"
        )
    if execution_mode not in ALLOWED_MODES:
        raise ValueError(
            f"invalid execution_mode: {execution_mode!r}"
        )
    routers_csv = ",".join(
        str(int(r)) for r in (requested_router_ids or ())
    )
    confirmations_json = json.dumps(
        sorted({str(c) for c in (confirmations or ())}),
        ensure_ascii=False,
    )
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_change_sets
                (tenant_id, service, policy_id, action_type,
                 parent_change_set_id, execution_mode, status,
                 preview_hash, health_score, health_grade,
                 risk_level, snapshot_id,
                 requested_router_ids, confirmations_json,
                 dry_run, created_by, created_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, 'planned',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id), service, int(policy_id),
                action_type,
                int(parent_change_set_id)
                    if parent_change_set_id is not None else None,
                execution_mode,
                str(preview_hash)[:128],
                int(health_score),
                str(health_grade)[:30],
                str(risk_level)[:20],
                int(snapshot_id) if snapshot_id is not None else None,
                routers_csv,
                confirmations_json,
                1 if dry_run else 0,
                str(created_by)[:120],
                now,
                str(notes)[:500],
            ),
        )
        return int(cur.lastrowid)


def get(tenant_id: int, change_set_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_change_sets "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(change_set_id)),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["confirmations"] = json.loads(
            out.get("confirmations_json") or "[]"
        )
    except (TypeError, ValueError):
        out["confirmations"] = []
    return out


def list_for_policy(
    tenant_id: int, *,
    service: str, policy_id: int,
    limit: int = 50,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM npc_change_sets "
        "WHERE tenant_id=? AND service=? AND policy_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(tenant_id), service, int(policy_id),
         int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def update_status(
    tenant_id: int, change_set_id: int, *,
    status: str,
    error_message: str = "",
    finished_at_now: bool = False,
    executed_at_now: bool = False,
    rolled_back_at_now: bool = False,
) -> Optional[dict]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    bits = ["status=?"]
    params: list[Any] = [status]
    if error_message:
        bits.append("error_message=?")
        params.append(str(error_message)[:2000])
    if executed_at_now:
        bits.append("executed_at=?")
        params.append(_now())
    if finished_at_now:
        bits.append("finished_at=?")
        params.append(_now())
    if rolled_back_at_now:
        bits.append("rolled_back_at=?")
        params.append(_now())
    params.extend([int(tenant_id), int(change_set_id)])
    with transaction() as c:
        c.execute(
            f"UPDATE npc_change_sets SET {', '.join(bits)} "
            "WHERE tenant_id=? AND id=?",
            tuple(params),
        )
    return get(tenant_id, change_set_id)


# ─── Per-router targets ─────────────────────────────────────


def add_target(
    *, change_set_id: int, tenant_id: int, router_id: int,
    rendered_script: str = "",
    rollback_script: str = "",
    status: str = TARGET_STATUS_PENDING,
) -> int:
    if status not in ALLOWED_TARGET_STATUSES:
        raise ValueError(f"invalid target status: {status!r}")
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_change_set_targets
                (change_set_id, tenant_id, router_id,
                 status, rendered_script, rollback_script)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(change_set_id), int(tenant_id),
                int(router_id), status,
                rendered_script, rollback_script,
            ),
        )
        return int(cur.lastrowid)


def list_targets(
    change_set_id: int,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM npc_change_set_targets "
        "WHERE change_set_id=? ORDER BY id",
        (int(change_set_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def update_target(
    target_id: int, *,
    status: Optional[str] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    error_message: Optional[str] = None,
    started_at_now: bool = False,
    finished_at_now: bool = False,
) -> Optional[dict]:
    if status is not None and status not in ALLOWED_TARGET_STATUSES:
        raise ValueError(f"invalid target status: {status!r}")
    bits: list[str] = []
    params: list[Any] = []
    if status is not None:
        bits.append("status=?")
        params.append(status)
    if stdout is not None:
        bits.append("stdout=?")
        params.append(str(stdout)[:8000])
    if stderr is not None:
        bits.append("stderr=?")
        params.append(str(stderr)[:8000])
    if error_message is not None:
        bits.append("error_message=?")
        params.append(str(error_message)[:2000])
    if started_at_now:
        bits.append("started_at=?")
        params.append(_now())
    if finished_at_now:
        bits.append("finished_at=?")
        params.append(_now())
    if not bits:
        return get_target(target_id)
    params.append(int(target_id))
    with transaction() as c:
        c.execute(
            f"UPDATE npc_change_set_targets "
            f"SET {', '.join(bits)} WHERE id=?",
            tuple(params),
        )
    return get_target(target_id)


def get_target(target_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_change_set_targets WHERE id=?",
        (int(target_id),),
    ).fetchone()
    return dict(row) if row else None


__all__ = [
    "ACTION_APPLY", "ACTION_ROLLBACK", "ALLOWED_ACTIONS",
    "STATUS_PLANNED", "STATUS_RUNNING",
    "STATUS_SUCCEEDED", "STATUS_FAILED",
    "STATUS_PARTIALLY_SUCCEEDED",
    "STATUS_ROLLED_BACK",
    "STATUS_ROLLBACK_PENDING", "STATUS_ROLLBACK_RUNNING",
    "STATUS_ROLLBACK_FAILED",
    "STATUS_PARTIALLY_ROLLED_BACK",
    "ALLOWED_STATUSES",
    "TARGET_STATUS_PENDING", "TARGET_STATUS_RUNNING",
    "TARGET_STATUS_SUCCEEDED", "TARGET_STATUS_FAILED",
    "TARGET_STATUS_SKIPPED", "TARGET_STATUS_ROLLED_BACK",
    "ALLOWED_TARGET_STATUSES",
    "MODE_CANARY", "MODE_STAGED", "MODE_FULL", "MODE_ROLLBACK",
    "ALLOWED_MODES",
    "create", "get", "list_for_policy", "update_status",
    "add_target", "list_targets",
    "update_target", "get_target",
]
