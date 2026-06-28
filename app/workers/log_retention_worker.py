"""log_retention_worker — periodic hard-pruning of high-volume log tables + VACUUM.

The append-only telemetry/accounting/event-log tables (radacct, business_events,
audit_log, the network_device_* logs, …) have no retention and SQLite never
reclaims free pages on its own, so the database grows without bound — the
dominant cause of a multi-hundred-MB DB on a long-running install. This worker
runs the retention service on a slow cadence (daily by default) to cap that
growth and physically reclaim the space.

It runs an initial pass shortly after startup (so a freshly-fixed install gets
relief immediately) and then once per interval. VACUUM inside the service only
fires when rows were actually deleted, so frequent restarts don't trigger a
VACUUM storm.

Env:
  HOBERADIUS_LOG_RETENTION_WORKER_ENABLED   default "1" (enabled)
  HOBERADIUS_LOG_RETENTION_INTERVAL_SECONDS default 86400 (daily), min 3600
  HOBERADIUS_NO_WORKER=1 / PYTEST_CURRENT_TEST disable it entirely
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "log_retention_worker"

_started = False
_lock = threading.Lock()

# Wait a short while after boot before the first pass, so startup isn't competing
# with a VACUUM and a brief restart loop never hammers the DB.
_INITIAL_DELAY_SEC = 120


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_LOG_RETENTION_WORKER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _interval() -> int:
    try:
        raw = int(os.environ.get("HOBERADIUS_LOG_RETENTION_INTERVAL_SECONDS", "86400") or 86400)
    except ValueError:
        raw = 86400
    return max(3600, raw)


def _loop(interval: int) -> None:
    time.sleep(_INITIAL_DELAY_SEC)
    while True:
        result = {}
        try:
            from app.radius.services import log_retention
            result = log_retention.run_retention(actor="system:log-retention-worker")
            if result.get("total_deleted"):
                _LOG.info(
                    "log_retention: deleted=%d vacuum=%s reclaimed=%dB",
                    result.get("total_deleted", 0),
                    result.get("vacuum_ran"),
                    result.get("reclaimed_bytes", 0),
                )
        except Exception:  # noqa: BLE001 — a retention error must never kill the thread
            _LOG.exception("log_retention tick failed")
        beat(_NAME, info={
            "interval_sec": interval,
            "last_deleted": result.get("total_deleted", 0),
            "last_reclaimed_bytes": result.get("reclaimed_bytes", 0),
        })
        time.sleep(interval)


def start_log_retention_worker() -> None:
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    with _lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("log_retention_worker disabled by env")
            return
        interval = _interval()
        threading.Thread(
            target=_loop, args=(interval,), daemon=True, name="hoberadius-log-retention"
        ).start()
        _started = True
        _LOG.info("log_retention_worker started — interval=%ds", interval)
