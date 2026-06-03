"""temp_speed_expiry_worker — immediate auto-revert of temporary speeds.

The legacy temp-speed expiry was *lazy*: it only ran when someone opened the
«المتصلون الآن» page, and it pushed no CoA — so a throttled live session stayed
throttled past its window until the user re-authenticated.

This worker fires every few seconds (default 10s), and for every subscriber
whose temp-speed window has ended it pushes a **revert CoA** restoring the
normal (override-or-plan) rate to the live session immediately, then clears the
flag. The actual logic lives in
:func:`app.radius.services.temp_speed.expire_due_temp_speeds`, which the online
page's on-load sweep calls too — one code path, so a page load and the worker
can't disagree.

Disable with ``HOBERADIUS_TEMP_SPEED_ENABLED=0``; tune cadence with
``HOBERADIUS_TEMP_SPEED_INTERVAL_SEC`` (min 5s). Skipped entirely under
``HOBERADIUS_NO_WORKER`` / pytest, like the other workers.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "temp_speed_expiry"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 10
_MIN_INTERVAL = 5


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_TEMP_SPEED_INTERVAL_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_TEMP_SPEED_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001 — table may be missing very early in boot
        return [1]


def expire_once() -> dict:
    """One sweep across all tenants. Public + side-effect-free to import, so
    tests can call it directly. Returns ``{"reverted": N}``."""
    from app.radius.services.temp_speed import expire_due_temp_speeds
    now = datetime.utcnow()
    total = 0
    for tenant_id in _all_tenants():
        try:
            total += expire_due_temp_speeds(tenant_id=tenant_id, now=now)
        except Exception:  # noqa: BLE001 — one tenant must not stall the rest
            _LOG.exception("temp-speed expiry failed for tenant %s", tenant_id)
    return {"reverted": total}


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("temp_speed_expiry started — interval=%ds", interval_sec)
    while True:
        stats = {"reverted": 0}
        try:
            stats = expire_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("temp_speed_expiry tick failed")
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_reverted": stats.get("reverted", 0)})
        time.sleep(interval_sec)


def start_temp_speed_expiry() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("temp_speed_expiry disabled by HOBERADIUS_TEMP_SPEED_ENABLED")
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": interval},
            daemon=True, name="hr-temp-speed-expiry",
        )
        t.start()
        _started = True


__all__ = ["start_temp_speed_expiry", "expire_once"]
