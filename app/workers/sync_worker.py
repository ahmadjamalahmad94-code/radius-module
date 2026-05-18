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


def _run_loop(interval_sec: float = 3.0) -> None:
    _LOG.info("sync_worker started, polling every %.1fs", interval_sec)
    while True:
        processed = 0
        try:
            jobs = sync_queue_repo.pick_due(limit=10)
            for j in jobs:
                try:
                    _process(j)
                    processed += 1
                except Exception:  # noqa: BLE001
                    _LOG.exception("sync_worker job=%d crashed (worker continues)", j.get("id"))
        except Exception:  # noqa: BLE001
            _LOG.exception("sync_worker tick failed")
        beat(_NAME, info={"interval_sec": interval_sec, "last_processed": processed})
        time.sleep(interval_sec)


def _process(job: dict) -> None:
    job_id = job["id"]
    sync_queue_repo.mark_syncing(job_id)
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
