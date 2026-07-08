"""schedule_window_worker — enforce connection-schedule windows on LIVE sessions.

The RADIUS *authorize* path rejects a login made outside a subscriber/offer's
allowed-hours window, and now also sets a ``Session-Timeout`` that closes the
session exactly at the window boundary (``policy_engine`` +
``services.schedule_window``). This worker is the complementary backstop: it
sweeps already-open radacct sessions on a short cadence and CoA-disconnects any
whose effective window has since CLOSED, with reason «خارج وقت السماح». It
catches sessions that were live before the boundary and long-lived sessions the
NAS never re-authed (or that ignored Session-Timeout).

Design mirrors the other workers: a bounded daemon loop, per-tenant/per-session
failure isolation (one bad router can't stall the rest), a heartbeat each tick,
and skipped entirely under HOBERADIUS_NO_WORKER / pytest.

Disable with HOBERADIUS_SCHEDULE_WINDOW_WORKER_ENABLED=0; tune cadence with
HOBERADIUS_SCHEDULE_WINDOW_INTERVAL_SEC (min 30s, default 60s).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "schedule_window"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 60
_MIN_INTERVAL = 30


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_SCHEDULE_WINDOW_INTERVAL_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_SCHEDULE_WINDOW_WORKER_ENABLED")
           or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def tick_once() -> dict:
    """One sweep across all active tenants. Public + import-safe so tests can
    call it directly. Returns the enforcer stats dict. Fully defensive: the
    import is inside the guard so a module-teardown race (sys.modules cleared by
    a concurrent test) can't raise an unhandled thread exception."""
    try:
        from app.radius.services import schedule_window
        return schedule_window.enforce_active_session_windows()
    except Exception:  # noqa: BLE001 — a sweep failure must not crash the loop
        _LOG.exception("schedule_window tick failed")
        return {"checked": 0, "out_of_window": 0, "disconnected": 0, "failed": 0}


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("schedule_window worker started — interval=%ds", interval_sec)
    while True:
        stats = tick_once()
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_out_of_window": stats.get("out_of_window", 0),
                          "last_disconnected": stats.get("disconnected", 0)})
        time.sleep(interval_sec)


def start_schedule_window_worker() -> None:
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("schedule_window worker disabled by "
                      "HOBERADIUS_SCHEDULE_WINDOW_WORKER_ENABLED")
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop, kwargs={"interval_sec": interval},
            daemon=True, name="hr-schedule-window",
        )
        t.start()
        _started = True


__all__ = ["start_schedule_window_worker", "tick_once"]
