"""System operations API for Flutter/Web parity."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.tenant import DEFAULT_TENANT_ID
from ...radius.db.connection import db, transaction
from ...radius.db.helpers import now_iso
from ...radius.db.repos import sync_queue_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _job(tenant_id: int, job_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM sync_queue WHERE tenant_id = ? AND id = ?",
        (tenant_id, job_id),
    ).fetchone()
    if not row:
        return None
    # Reuse the public list serializer from the repo to keep shape stable.
    for item in sync_queue_repo.list_jobs(tenant_id, limit=1000):
        if int(item["id"]) == int(job_id):
            return item
    return {"id": job_id, "tenant_id": tenant_id}


def _limit(default: int = 200, maximum: int = 1000) -> int:
    raw = request.args.get("limit")
    if not raw:
        return default
    try:
        return min(max(int(raw), 1), maximum)
    except ValueError:
        return default


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/system/status",
        "system_status",
        require_api_token(system_status),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/diagnostics",
        "system_diagnostics",
        require_api_token(system_diagnostics),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/sync",
        "system_sync_list",
        require_api_token(system_sync_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/system/sync/<int:job_id>/retry",
        "system_sync_retry",
        require_api_token(system_sync_retry),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/sync/<int:job_id>/cancel",
        "system_sync_cancel",
        require_api_token(system_sync_cancel),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/system/reconcile",
        "system_reconcile",
        require_api_token(system_reconcile),
        methods=["POST"],
    )


def system_status():
    from ...radius.routes.status import _gather_status

    return ok(_gather_status(_tid()))


def system_diagnostics():
    from ...radius.services import mt_diagnostics

    return ok(mt_diagnostics.diagnose_tenant(_tid()))


def system_sync_list():
    status = request.args.get("status") or None
    tenant_id = _tid()
    return ok(
        {
            "items": sync_queue_repo.list_jobs(
                tenant_id,
                status=status,
                limit=_limit(default=300),
            ),
            "stats": sync_queue_repo.stats(tenant_id),
            "status": status or "all",
        }
    )


def system_sync_retry(job_id: int):
    tenant_id = _tid()
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE sync_queue
            SET status='queued', next_attempt_at=?, last_error=''
            WHERE tenant_id = ? AND id = ?
            """,
            (now_iso(), tenant_id, job_id),
        )
        if cur.rowcount == 0:
            return fail("not_found", "Sync job not found", status=404)
    return ok({"job": _job(tenant_id, job_id), "action": "retry"})


def system_sync_cancel(job_id: int):
    tenant_id = _tid()
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE sync_queue
            SET status='failed', last_error='canceled by admin'
            WHERE tenant_id = ? AND id = ? AND status IN ('queued', 'retrying')
            """,
            (tenant_id, job_id),
        )
        if cur.rowcount == 0:
            return fail("not_found", "Sync job not found or not cancellable", status=404)
    return ok({"job": _job(tenant_id, job_id), "action": "cancel"})


def system_reconcile():
    try:
        from app.workers import mt_reconciler

        stats = mt_reconciler.reconcile_once()
    except Exception as exc:  # noqa: BLE001
        return fail("reconcile_failed", str(exc), status=500)
    return ok({"stats": stats})
