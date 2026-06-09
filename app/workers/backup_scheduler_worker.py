"""Background automatic-backup scheduler.

Polls every few minutes; when the tenant's saved schedule is due (controlled
from the backups page UI, not env), it runs a full backup (local → panel →
Drive). Enabling/disabling and the interval live in DB settings, so the worker
itself just needs to be running — it does nothing until the user turns the
schedule on.
"""
from __future__ import annotations

import os
import threading
import time

_started = False
_lock = threading.Lock()


def _loop(poll_interval: int) -> None:
    from app.radius.services.operations import get_operations_service

    while True:
        try:
            svc = get_operations_service()
            if svc.schedule_due(tenant_id=1):
                svc.run_full_backup(tenant_id=1, actor="system:backup-scheduler")
                svc.mark_schedule_ran(tenant_id=1)
        except Exception:
            # Never let a scheduling error kill the worker thread.
            pass
        time.sleep(poll_interval)


def start_backup_scheduler_worker() -> None:
    """Start the polling thread (unless workers are globally disabled)."""
    global _started
    # Never run automatic backups when workers are disabled or under pytest, so a
    # test run can never trigger a scheduled backup.
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    with _lock:
        if _started:
            return
        poll_interval = max(
            60,
            int(os.environ.get("HOBERADIUS_BACKUP_SCHEDULER_INTERVAL_SECONDS", "300") or 300),
        )
        threading.Thread(
            target=_loop, args=(poll_interval,), daemon=True, name="hoberadius-backup-scheduler"
        ).start()
        _started = True
