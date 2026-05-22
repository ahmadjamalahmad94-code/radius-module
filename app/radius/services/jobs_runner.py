"""jobs_runner — S1.2 background-job runner abstraction.

The minimum surface a caller needs to enqueue + execute work:

    @register_handler("mt.diag.scan")
    def _scan(job, payload):
        ...  # do the work
        return {"signals": [...]}    # becomes result_json

    jid = enqueue(tenant_id=..., type="mt.diag.scan",
                  payload={"router_id": 42})
    run_job(jid)            # synchronous in dev / test;
                            # a future worker calls the same
                            # function from a different process.

The runner does three things and nothing else:

  1. Read the job row, refuse to run anything not in `queued`.
  2. Look up the handler by `type`, fail loudly if missing.
  3. Drive the lifecycle: mark_running → call handler →
     mark_success / mark_failed depending on outcome.

What this is NOT — and intentionally so for now:

  * Redis / Celery / RQ. The project has no broker. The shape
    of the API is queue-ready (handlers are registered by
    `type`, `run_job` takes only a job_id) so a future worker
    process can adopt it by importing `run_job(jid)` and calling
    it on every dequeued message. Adding the broker is a
    separate, opt-in commit.
  * Concurrency primitives. One job → one synchronous call.
    The print-export worker that ships in S1.3 will be the
    first real production-style consumer.
  * Retry / backoff. A `failed` job stays failed; an operator
    can re-enqueue with the same payload if they want a retry.
    Adding auto-retry would hide real router issues — defer.

Handler contract:
  - Signature: `handler(job_row, payload) -> dict | None`
  - Return value (a dict) is stored as the success result; the
    repo redacts secrets in it before storage.
  - Raise any exception → the runner catches it, marks failed,
    writes str(e) to error_message. Don't swallow inside the
    handler unless you want a successful row with no errors.
  - Handlers may call `progress(job_row["id"], 42, "step")` to
    update a progress bar without committing to the runner
    layer.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable, Mapping

from ..db.repos import jobs_repo


# ─── Registry ─────────────────────────────────────────────────


_HANDLERS: dict[str, Callable[[dict, dict], Any]] = {}


def register_handler(
    job_type: str,
) -> Callable[[Callable[[dict, dict], Any]],
              Callable[[dict, dict], Any]]:
    """Decorator: bind a handler function to a job type.

        @register_handler("mt.backup.save")
        def _backup(job, payload): ...
    """
    def _wrap(fn: Callable[[dict, dict], Any]):
        if not job_type or not job_type.strip():
            raise ValueError("job type required")
        _HANDLERS[job_type] = fn
        return fn
    return _wrap


def known_job_types() -> list[str]:
    """Read-only view of the currently registered types — handy
    for `/jobs` UIs that want a "filter by type" dropdown."""
    return sorted(_HANDLERS.keys())


def _reset_handlers_for_tests() -> None:
    """Test hook so a unit test can wipe the registry between
    cases. NOT exported via __all__; tests import by name."""
    _HANDLERS.clear()


# ─── Public API ───────────────────────────────────────────────


def enqueue(
    *, tenant_id: int, type: str,
    payload: Mapping[str, Any] | None = None,
    owner_admin_id: int | None = None,
    router_id: int | None = None,
) -> int:
    """Persist a new job row. Status = queued. Returns id."""
    return jobs_repo.create(
        tenant_id=int(tenant_id),
        type=str(type).strip(),
        payload=dict(payload or {}),
        owner_admin_id=owner_admin_id,
        router_id=router_id,
    )


def progress(
    job_id: int, percent: int, step: str = "",
) -> None:
    """Surface-level alias so handler code reads naturally:

        progress(job_row["id"], 50, "نصف الطريق")
    """
    jobs_repo.update_progress(job_id, percent=percent, step=step)


class UnknownJobType(Exception):
    """No handler registered for this `type`. The job is left
    untouched so an operator can rectify (register the handler
    or cancel) instead of being silently failed."""


def run_job(job_id: int) -> dict:
    """Drive one job to a terminal state.

    Returns the post-run row dict so a synchronous caller can
    inspect the outcome without a second `get()`. Idempotent on
    already-terminal jobs (just returns the row).

    Concurrency note — when a real worker arrives, this function
    is the unit it dequeues. Keep its semantics narrow.
    """
    row = jobs_repo.get(job_id)
    if not row:
        raise ValueError(f"job {job_id} not found")

    status = row["status"]
    if status in jobs_repo.TERMINAL_STATUSES:
        # Idempotent: re-running a terminal job is a no-op so a
        # crash-recovered worker doesn't re-execute completed work.
        return row
    if status == jobs_repo.JOB_STATUS_CANCELLED:
        return row

    handler = _HANDLERS.get(row["type"])
    if handler is None:
        # Don't mark the job failed — the handler might be
        # registered by a module that imports later. Tell the
        # caller via exception.
        raise UnknownJobType(row["type"])

    if status == jobs_repo.JOB_STATUS_QUEUED:
        jobs_repo.mark_running(job_id)

    try:
        result = handler(row, row.get("payload") or {})
    except Exception as exc:  # noqa: BLE001
        # Capture the chain so an operator can see *where* it broke.
        tb = traceback.format_exc(limit=8)
        jobs_repo.mark_failed(
            job_id,
            error=str(exc),
            result={"traceback": tb},
        )
        return jobs_repo.get(job_id)  # type: ignore[return-value]

    jobs_repo.mark_success(
        job_id,
        result=dict(result) if isinstance(result, dict) else
               ({"value": result} if result is not None else {}),
    )
    return jobs_repo.get(job_id)  # type: ignore[return-value]


__all__ = [
    "register_handler",
    "known_job_types",
    "enqueue",
    "progress",
    "run_job",
    "UnknownJobType",
]
