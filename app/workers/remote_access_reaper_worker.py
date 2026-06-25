"""remote_access_reaper_worker — auto-closes expired «Open WinBox» forwards.

Each remote-access session has an absolute ``expires_at``. This worker sweeps
every few seconds and closes any that have passed it: it marks the rows expired,
rewrites the nginx-stream config (dropping those forwards) + touches the reload
marker, and audits the auto-close. So a WinBox forward is NEVER left open past
its window even if the admin forgets to close it — the security guarantee.

The sweep logic lives in
:func:`app.radius.services.router_remote_access.sweep_expired`; the per-router
page's close button and this worker share that one code path. Disable with
``HOBERADIUS_REMOTE_ACCESS_ENABLED=0``; tune cadence with
``HOBERADIUS_REMOTE_ACCESS_SWEEP_SEC`` (min 10s). Skipped under
``HOBERADIUS_NO_WORKER`` / pytest like the other workers.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "remote_access_reaper"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 30
_MIN_INTERVAL = 10


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_REMOTE_ACCESS_SWEEP_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_REMOTE_ACCESS_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def sweep_once() -> dict:
    """One sweep. Public + import-safe so tests can call it directly. Returns
    ``{"closed": N}``."""
    from app.radius.services.router_remote_access import sweep_expired
    try:
        n = sweep_expired()
    except Exception:  # noqa: BLE001 — never let the loop die
        _LOG.exception("remote-access reaper sweep failed")
        n = 0
    return {"closed": n}


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("remote_access_reaper started — interval=%ds", interval_sec)
    while True:
        stats = sweep_once()
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_closed": stats.get("closed", 0)})
        time.sleep(interval_sec)


def start_remote_access_reaper() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("remote_access_reaper disabled by HOBERADIUS_REMOTE_ACCESS_ENABLED")
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop, kwargs={"interval_sec": interval},
            daemon=True, name="hr-remote-access-reaper",
        )
        t.start()
        _started = True


__all__ = ["start_remote_access_reaper", "sweep_once"]
