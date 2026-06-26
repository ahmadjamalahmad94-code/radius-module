"""Per-router reachability circuit-breaker.

THE PROBLEM THIS SOLVES
-----------------------
RouterOS API calls are synchronous and block a gunicorn worker thread.
When a router is unreachable (powered off, link down, route black-holed),
a TCP connect attempt hangs until the connect timeout. With only a few
worker threads, a single dead router probed on every page load drains
the thread pool and the WHOLE panel returns 504 — even pages that never
touch that router.

THE FIX
-------
Once a dial to a router fails on the network layer, mark that router
"unreachable" for a short TTL. While the breaker is OPEN, subsequent
dials short-circuit INSTANTLY (no socket, no thread held) instead of
each paying a full connect timeout. The first dial after the TTL
expires is allowed through as a probe (half-open); success closes the
breaker, failure re-opens it.

SCOPE OF "FAILURE"
------------------
Only NETWORK failures open the breaker (connect refused/timeout, broken
pipe, OSError). An auth failure or a router-side trap means the router
RESPONDED — it is reachable — so those must NOT open the breaker; they
call record_success() instead.

STATE
-----
Module-level, per-process, thread-safe. gunicorn runs a single worker
process (background workers are in-process singletons), so one shared
dict is exactly the right scope. Times use a monotonic clock; tests may
inject `now` for determinism.
"""
from __future__ import annotations

import os
import threading
import time

# Default: a dead router stays "known-unreachable" for 45s, so it costs
# at most one connect-timeout per 45s window instead of one per request.
_DEFAULT_TTL_SEC = 45.0


def _ttl_sec() -> float:
    """Breaker open-duration (seconds). Env-overridable for ops tuning."""
    raw = os.environ.get("HOBERADIUS_MT_UNREACHABLE_TTL_SEC")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_TTL_SEC
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SEC
    return v if v > 0 else _DEFAULT_TTL_SEC


_lock = threading.Lock()
# router_id -> monotonic deadline until which the router is unreachable.
_open_until: dict[int, float] = {}
# router_id -> consecutive network-failure count (observability only).
_failures: dict[int, int] = {}


def _now(injected: float | None) -> float:
    return time.monotonic() if injected is None else float(injected)


def is_unreachable(router_id: int | None, *, now: float | None = None) -> bool:
    """True if the breaker is currently OPEN for this router.

    On TTL expiry this returns False AND clears the entry (half-open):
    the caller is allowed one probe. record_failure() re-opens it if the
    probe fails; record_success() keeps it closed.
    """
    if not router_id:
        return False
    rid = int(router_id)
    t = _now(now)
    with _lock:
        deadline = _open_until.get(rid)
        if deadline is None:
            return False
        if t >= deadline:
            # Half-open: let exactly the path that reads this expiry probe.
            _open_until.pop(rid, None)
            return False
        return True


def record_failure(router_id: int | None, *, now: float | None = None) -> None:
    """Open (or re-arm) the breaker after a NETWORK-level dial failure."""
    if not router_id:
        return
    rid = int(router_id)
    t = _now(now)
    with _lock:
        _open_until[rid] = t + _ttl_sec()
        _failures[rid] = _failures.get(rid, 0) + 1


def record_success(router_id: int | None) -> None:
    """Close the breaker — the router answered (data, auth error, or
    even a trap all prove reachability)."""
    if not router_id:
        return
    rid = int(router_id)
    with _lock:
        _open_until.pop(rid, None)
        _failures.pop(rid, None)


def state(router_id: int | None, *, now: float | None = None) -> dict:
    """Snapshot for observability / tests."""
    rid = int(router_id or 0)
    t = _now(now)
    with _lock:
        deadline = _open_until.get(rid)
        is_open = deadline is not None and t < deadline
        return {
            "router_id": rid,
            "unreachable": is_open,
            "retry_in_sec": max(0.0, deadline - t) if (deadline and is_open) else 0.0,
            "failures": _failures.get(rid, 0),
        }


def snapshot() -> dict[int, dict]:
    """All currently-OPEN routers — used by health/observability views."""
    t = time.monotonic()
    with _lock:
        out: dict[int, dict] = {}
        for rid, deadline in list(_open_until.items()):
            if t < deadline:
                out[rid] = {
                    "unreachable": True,
                    "retry_in_sec": max(0.0, deadline - t),
                    "failures": _failures.get(rid, 0),
                }
        return out


def reset() -> None:
    """Clear all breaker state (tests / full app restart)."""
    with _lock:
        _open_until.clear()
        _failures.clear()


__all__ = [
    "is_unreachable",
    "record_failure",
    "record_success",
    "state",
    "snapshot",
    "reset",
]
