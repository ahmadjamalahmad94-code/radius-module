"""self_update_worker — periodic "is a newer version available?" check.

Follows the project worker idiom: a pure, testable ``poll_once()`` plus a
daemon thread guarded by ``HOBERADIUS_NO_WORKER``. The DB layer is
thread-local (see db/connection.py), so no Flask app/request context is
needed — the worker calls the settings repo directly.

Cadence: ``HOBERADIUS_UPDATE_CHECK_INTERVAL_SECONDS`` (env) or the
``self_update.check_interval_seconds`` setting, default 6h, floor 5min. The
check itself degrades silently when the bridge is disabled/unreachable, so
this loop is safe to run on every instance regardless of configuration.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "self_update_worker"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 6 * 60 * 60   # 6 hours
_MIN_INTERVAL = 5 * 60            # 5 minutes


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_UPDATE_CHECK_INTERVAL_SECONDS", "")
    if not raw.strip():
        try:
            from app.radius.services.self_update import SK_CHECK_INTERVAL
            from app.radius.db.repos import tenants_repo
            raw = str(tenants_repo.get_setting(1, SK_CHECK_INTERVAL, "") or "")
        except Exception:  # noqa: BLE001
            raw = ""
    try:
        return max(int(raw), _MIN_INTERVAL)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL


def _tenant_ids() -> list[int]:
    """Every tenant that has a row — default single-tenant → [1]."""
    try:
        from app.radius.db.connection import db
        cur = db().execute("SELECT id FROM tenants ORDER BY id")
        ids = [int(r["id"]) for r in cur.fetchall()]
        return ids or [1]
    except Exception:  # noqa: BLE001
        return [1]


def poll_once() -> dict:
    """Run the update check for every tenant. Pure + testable; never raises."""
    from app.radius.services import self_update

    checked = 0
    available = 0
    for tid in _tenant_ids():
        try:
            state = self_update.check_for_update(tid)
            checked += 1
            if state.get("available"):
                available += 1
        except Exception:  # noqa: BLE001 — one tenant's failure must not stop the rest
            _LOG.warning("self_update check failed for tenant %s", tid, exc_info=True)
    return {"checked": checked, "available": available}


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("self_update worker started (interval=%ss)", interval_sec)
    # Small initial delay so boot isn't blocked by a network round-trip.
    time.sleep(15)
    while True:
        stats = {"checked": 0, "available": 0}
        try:
            stats = poll_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("self_update worker tick failed")
        interval = _interval_sec()
        beat(_NAME, info={"interval_sec": interval, **stats})
        time.sleep(interval)


def start_self_update_worker(flask_app=None) -> None:  # noqa: ANN001 — parity w/ others
    """Start the periodic update-check thread (once per process)."""
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1":
        return
    with _started_lock:
        if _started:
            return
        thread = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": _interval_sec()},
            daemon=True,
            name="hr-self-update-check",
        )
        thread.start()
        _started = True


__all__ = ["poll_once", "start_self_update_worker"]
