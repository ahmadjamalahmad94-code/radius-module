"""
sync_worker — يلتقط jobs من sync_queue ويُنفّذها على MikroTik.

يعمل في خيط خلفي مستقل (يُبدأ مرة واحدة من _start_workers).
"""
from __future__ import annotations

import logging
import threading
import time

from app.radius.db.repos import sync_queue_repo
from app.radius.integration import router_sync

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "sync_worker"

_started = False
_started_lock = threading.Lock()


# Stress L01 (2026-09-28): 10 jobs / 3 s (3.3 jobs/s, 3 commits each) left a
# 22.6k backlog ≈ 1.9 h behind. Now: batches of _BATCH, back-to-back while the
# queue is full (up to _MAX_BATCHES_PER_TICK, then a short breather), and jobs
# of tenants WITHOUT an enabled router — guaranteed no-ops — are closed in one
# transaction per batch instead of two commits each.
_BATCH = 100
_MAX_BATCHES_PER_TICK = 20


def run_tick() -> int:
    """Drain what is due now (bounded). Returns the number of jobs handled."""
    processed = 0
    for _ in range(_MAX_BATCHES_PER_TICK):
        jobs = sync_queue_repo.pick_due(limit=_BATCH)
        if not jobs:
            break
        noop_ids: list[int] = []
        real: list[dict] = []
        for j in jobs:
            if router_sync.tenant_has_sync_targets(j["tenant_id"]):
                real.append(j)
            else:
                noop_ids.append(j["id"])
        if noop_ids:
            processed += sync_queue_repo.mark_done_many(noop_ids)
        for j in real:
            try:
                _process(j)
                processed += 1
            except Exception:  # noqa: BLE001
                _LOG.exception("sync_worker job=%d crashed (worker continues)", j.get("id"))
        if len(jobs) < _BATCH:
            break
    return processed


def _run_loop(interval_sec: float = 3.0) -> None:
    _LOG.info("sync_worker started, polling every %.1fs", interval_sec)
    while True:
        processed = 0
        try:
            processed = run_tick()
        except Exception:  # noqa: BLE001
            _LOG.exception("sync_worker tick failed")
        beat(_NAME, info={"interval_sec": interval_sec, "last_processed": processed})
        time.sleep(interval_sec)


def _process(job: dict) -> None:
    job_id = job["id"]
    # claim = mark «syncing» + re-read: the payload may have been coalesced
    # with a newer snapshot after pick_due read it.
    fresh = sync_queue_repo.claim(job_id)
    if fresh is None:
        return  # taken/finished elsewhere
    job = fresh
    try:
        ok, err = router_sync.execute_job(job)
    except Exception as e:  # noqa: BLE001
        _LOG.exception("sync_worker job=%d crashed", job_id)
        sync_queue_repo.mark_failed(job_id, error=str(e))
        return
    if ok:
        sync_queue_repo.mark_done(job_id)
    else:
        sync_queue_repo.mark_failed(job_id, error=err)


def start_sync_worker() -> None:
    global _started
    with _started_lock:
        if _started: return
        t = threading.Thread(target=_run_loop, daemon=True, name="hr-sync-worker")
        t.start()
        _started = True
