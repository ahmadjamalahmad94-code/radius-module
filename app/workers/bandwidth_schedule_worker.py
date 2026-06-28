"""bandwidth_schedule_worker — auto-apply time-based speed schedules LIVE.

Owner decision (June 2026): «طالما أنا محدد وقت، يشتغل بالوقت المحدد ويتطبق على
كل الجلسات الحية». A bandwidth schedule must take effect on already-connected
sessions at its window boundaries, not only at the next re-auth.

Design — **transition-based, not per-tick**, so it is idempotent and never spams
CoA:

  * each tick computes, per schedule, whether NOW is inside its time window;
  * it acts only on a *transition* of that schedule's phase:
      - idle → engaged  (window just started, or worker started mid-window):
        recompute each in-scope user's EFFECTIVE rate and CoA it;
      - engaged → idle  (window ended, or schedule disabled/removed):
        recompute (the schedule no longer wins the cascade) and CoA the
        fallback rate — i.e. revert.
  * no transition ⇒ no CoA. New sessions that connect mid-window already get the
    schedule rate at auth (policy_engine), so they need no worker action.

Effective-rate recomputation (in :mod:`bandwidth_apply`) respects the full
cascade (active schedule > subscriber override > plan/profile base), so the
worker never fights a higher-precedence subscriber override.

Safety: per-tenant and per-schedule failures are isolated (one bad router/schedule
can't stall the rest); every engage/release is logged + audited; the loop sleeps
a bounded interval. Honors the global live gate (HOBERADIUS_ENABLE_LIVE_SPEED_APPLY,
default ON) — when off, it still tracks phase but only dry-logs (no CoA).

Disable the worker with HOBERADIUS_BW_SCHEDULE_WORKER_ENABLED=0; tune cadence with
HOBERADIUS_BW_SCHEDULE_INTERVAL_SEC (min 30s). Skipped under HOBERADIUS_NO_WORKER /
pytest like the other workers.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "bandwidth_schedule"

_started = False
_started_lock = threading.Lock()

# (tenant_id, schedule_id) → "engaged" | "idle". Process-local: on restart a
# schedule seen in-window for the first time re-engages (a safe, idempotent CoA
# to the same rate); one seen out-of-window simply records idle (no action).
_phase: dict[tuple[int, int], str] = {}
_phase_lock = threading.Lock()

_DEFAULT_INTERVAL = 60
_MIN_INTERVAL = 30


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_BW_SCHEDULE_INTERVAL_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_BW_SCHEDULE_WORKER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status='active'").fetchall()]
    except Exception:  # noqa: BLE001 — table may be missing very early in boot
        return [1]


def _is_in_window(schedule: dict, now_hm: str) -> bool:
    if not schedule.get("enabled"):
        return False
    from app.radius.db.repos.operations_repo import _in_time_window
    start = schedule.get("starts_at_time") or ""
    end = schedule.get("ends_at_time") or ""
    if not start or not end:
        return False
    try:
        return _in_time_window(now_hm, start, end)
    except Exception:  # noqa: BLE001 — malformed time must not crash the tick
        return False


def _tenant_tick(tenant_id: int, now: datetime) -> dict:
    """Process one tenant's schedules; act only on phase transitions."""
    from app.radius.db.repos import operations_repo
    from app.radius.services import bandwidth_apply

    now_hm = now.strftime("%H:%M")
    engaged = released = 0
    try:
        schedules = operations_repo.list_bandwidth_schedules(tenant_id, limit=1000)
    except Exception:  # noqa: BLE001
        _LOG.exception("schedule list failed for tenant %s", tenant_id)
        return {"engaged": 0, "released": 0}

    seen_keys: set[tuple[int, int]] = set()
    for sched in schedules:
        sid = int(sched.get("id") or 0)
        if not sid:
            continue
        key = (tenant_id, sid)
        seen_keys.add(key)
        in_window = _is_in_window(sched, now_hm)
        with _phase_lock:
            prev = _phase.get(key)
        try:
            if in_window and prev != "engaged":
                bandwidth_apply.apply_schedule_users_live(
                    tenant_id, sched, at=now, phase="engage")
                with _phase_lock:
                    _phase[key] = "engaged"
                engaged += 1
            elif (not in_window) and prev == "engaged":
                bandwidth_apply.apply_schedule_users_live(
                    tenant_id, sched, at=now, phase="release")
                with _phase_lock:
                    _phase[key] = "idle"
                released += 1
            else:
                with _phase_lock:
                    _phase[key] = "engaged" if in_window else "idle"
        except Exception:  # noqa: BLE001 — one schedule must not stall the rest
            _LOG.exception("schedule %s tick failed (tenant %s)", sid, tenant_id)

    # A schedule that was deleted while engaged: release it once, then forget.
    with _phase_lock:
        stale = [k for k, v in _phase.items()
                 if k[0] == tenant_id and k not in seen_keys and v == "engaged"]
    for key in stale:
        try:
            bandwidth_apply.apply_schedule_users_live(
                tenant_id, {"id": key[1], "target_type": "plan"}, at=now, phase="release")
            released += 1
        except Exception:  # noqa: BLE001
            _LOG.exception("stale release failed for %s", key)
        with _phase_lock:
            _phase.pop(key, None)

    return {"engaged": engaged, "released": released}


def tick_once(now: datetime | None = None) -> dict:
    """One sweep across all tenants. Public + import-safe so tests call it
    directly. Returns ``{"engaged": N, "released": M}``."""
    now = now or datetime.utcnow()
    engaged = released = 0
    for tenant_id in _all_tenants():
        try:
            stats = _tenant_tick(tenant_id, now)
            engaged += stats["engaged"]
            released += stats["released"]
        except Exception:  # noqa: BLE001 — one tenant must not stall the rest
            _LOG.exception("bandwidth_schedule tick failed for tenant %s", tenant_id)
    return {"engaged": engaged, "released": released}


def reset_state_for_tests() -> None:
    with _phase_lock:
        _phase.clear()


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("bandwidth_schedule worker started — interval=%ds", interval_sec)
    while True:
        stats = {"engaged": 0, "released": 0}
        try:
            stats = tick_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("bandwidth_schedule tick failed")
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_engaged": stats.get("engaged", 0),
                          "last_released": stats.get("released", 0)})
        time.sleep(interval_sec)


def start_bandwidth_schedule_worker() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("bandwidth_schedule worker disabled by "
                      "HOBERADIUS_BW_SCHEDULE_WORKER_ENABLED")
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop, kwargs={"interval_sec": interval},
            daemon=True, name="hr-bandwidth-schedule",
        )
        t.start()
        _started = True


__all__ = ["start_bandwidth_schedule_worker", "tick_once", "reset_state_for_tests"]
