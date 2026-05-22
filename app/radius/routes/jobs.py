"""S1.3 — Job endpoints for the operator UI.

Two responsibilities:

  POST /admin/radius/jobs/diagnostics/<nas_id>
       → enqueue + run (synchronous for now), return job_id or
         redirect to the status page.

  GET  /admin/radius/jobs/<job_id>
       → JSON or HTML view of the row. Tenant-scoped — a job
         from another tenant looks like a 404.

The existing synchronous /api/v1/mikrotik/<id>/health endpoint
stays as the dashboard's data source. This route is the seam
that lets operators run + persist a scan with full audit
history attached to a job row.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, g, jsonify, redirect, render_template,
    request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import jobs_repo
# Import handlers for the registry side-effect — without this
# the runner has no handler bound to "mt.diag.scan".
from ..services import jobs_handlers  # noqa: F401
from ..services import jobs_runner


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _wants_json() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def register_jobs_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/jobs/diagnostics/<int:nas_id>",
        "jobs_run_diagnostics", jobs_run_diagnostics,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/jobs/<int:job_id>",
        "jobs_show", jobs_show,
        methods=["GET"],
    )


def jobs_run_diagnostics(nas_id: int):
    """Kick off a diagnostics scan as a tracked job.

    Synchronous in this commit — `run_job(jid)` returns when the
    handler is done. When a worker process arrives later, the
    pattern stays the same: enqueue here, let the worker call
    run_job in its own loop. The HTTP response will then return
    202 + job_id immediately instead of waiting.
    """
    actor = getattr(g, "admin_id", None)
    jid = jobs_runner.enqueue(
        tenant_id=_tid(), type="mt.diag.scan",
        router_id=int(nas_id),
        owner_admin_id=int(actor) if actor is not None else None,
        payload={"router_id": int(nas_id)},
    )
    try:
        jobs_runner.run_job(jid)
    except jobs_runner.UnknownJobType:
        # Should never happen — the handler module is imported
        # at the top of this file. If it does, the row stays
        # queued and the operator sees that in the status page.
        pass

    if _wants_json():
        return jsonify({"job_id": jid,
                        "status_url":
                        url_for("radius.jobs_show", job_id=jid)}), 202
    return redirect(url_for("radius.jobs_show", job_id=jid))


def jobs_show(job_id: int):
    row = jobs_repo.get(int(job_id))
    if not row:
        abort(404)
    # Tenant isolation — never leak existence of other tenants'
    # jobs. Match audit_log scoping.
    if int(row.get("tenant_id") or 0) != _tid():
        abort(404)
    if _wants_json():
        return jsonify(row)
    return render_template("radius/job_detail.html", job=row)
