"""wal_maintenance_worker — keeps the SQLite WAL from growing without bound.

Stress L01 (2026-09-28): under sustained load the WAL reached 867 MB and never
shrank. Constant overlapping readers (panel threads + FreeRADIUS) starve the
automatic PASSIVE checkpoint of the moment it needs to reset the log, and the
file kept its high-water mark. Every minute this worker calls
``connection.wal_maintenance()``: a cheap PASSIVE checkpoint normally, and a
TRUNCATE checkpoint (short busy wait, retried next minute) once the WAL is
larger than 64 MB — so it lands on the first quiet moment.

Env:
  HOBERADIUS_WAL_MAINTENANCE_INTERVAL_SEC  default 60, min 10
  HOBERADIUS_WAL_TRUNCATE_MB               default 64
  HOBERADIUS_NO_WORKER=1 / PYTEST_CURRENT_TEST disable it (as every worker)
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "wal_maintenance"

_started = False
_lock = threading.Lock()

# First pass shortly after boot: shrinks a WAL left huge by a previous run.
_INITIAL_DELAY_SEC = 30


def _interval() -> int:
    try:
        raw = int(os.environ.get("HOBERADIUS_WAL_MAINTENANCE_INTERVAL_SEC", "60") or 60)
    except ValueError:
        raw = 60
    return max(10, raw)


def _truncate_above() -> int:
    try:
        mb = int(os.environ.get("HOBERADIUS_WAL_TRUNCATE_MB", "64") or 64)
    except ValueError:
        mb = 64
    return max(1, mb) * 1024 * 1024


def run_once() -> dict:
    """One maintenance pass (also used by tests). Never raises."""
    from app.radius.db.connection import wal_maintenance
    res = wal_maintenance(truncate_above=_truncate_above())
    if res.get("mode") == "TRUNCATE":
        if res.get("busy"):
            _LOG.info("wal_maintenance: WAL %.1f MB — TRUNCATE deferred (busy), "
                      "retrying next tick", (res.get("wal_before") or 0) / 1048576)
        else:
            _LOG.info("wal_maintenance: WAL truncated %.1f MB → %.1f MB",
                      (res.get("wal_before") or 0) / 1048576,
                      (res.get("wal_after") or 0) / 1048576)
    elif res.get("mode") == "error":
        _LOG.warning("wal_maintenance failed: %s", res.get("error"))
    return res


def _loop(interval: int) -> None:
    time.sleep(_INITIAL_DELAY_SEC)
    while True:
        res: dict = {}
        try:
            res = run_once()
        except Exception:  # noqa: BLE001 — never kill the thread
            _LOG.exception("wal_maintenance tick failed")
        beat(_NAME, info={
            "interval_sec": interval,
            "last_mode": res.get("mode"),
            "last_busy": res.get("busy"),
            "wal_bytes": res.get("wal_after"),
        })
        time.sleep(interval)


def start_wal_maintenance_worker() -> None:
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    with _lock:
        if _started:
            return
        interval = _interval()
        threading.Thread(target=_loop, args=(interval,), daemon=True,
                         name="hr-wal-maintenance").start()
        _started = True
        _LOG.info("wal_maintenance_worker started — interval=%ds", interval)
