"""K2 — admin-facing MikroTik client wrapper.

Wraps the existing wire-protocol client + pool with:

1. **Connection mode awareness.** Translates a `nas_devices` row
   into the right `router_cfg` for `mikrotik.pool.acquire`,
   honouring `connection_mode='vpn'` so the request goes to the
   VPN peer IP rather than the public address.

2. **TTL cache.** Stats endpoints (CPU, RAM, interface bytes) are
   read on every dashboard refresh. The cache holds the last
   result for 30 / 60 / 120 s so the router doesn't get pinged
   every second.

3. **Clean error envelope.** Every method returns a result tuple
   `(ok: bool, data: Any, error: str)` — never raises out into
   the route layer. The dashboard renders an inline "offline"
   chip when `ok=False`.

4. **Operation tags** for observability — every API call is logged
   with `(router_id, operation, dialed_address, mode, took_ms)`.

The existing low-level wire client (`MikrotikClient`) and pool
(`pool.acquire`) are reused as-is — this module is a thin
adapter, not a rewrite.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, TypeVar

from ..integration.mikrotik.errors import (
    AuthError,
    ConnectError,
    MikrotikError,
    MikrotikTrap,
)
from ..integration.mikrotik.pool import acquire as _pool_acquire
from .nas_connection import resolve_connection_address, resolve_connection_descriptor

_LOG = logging.getLogger(__name__)

T = TypeVar("T")


# Default cache TTLs (seconds). The K3+ endpoints pick the one
# that matches their volatility.
TTL_SYSTEM = 60.0       # CPU / RAM / version — stable
TTL_HEALTH = 30.0       # temperature can spike, but not faster
TTL_INTERFACES = 15.0   # bps interesting to watch
TTL_ACTIVE_USERS = 10.0 # disconnects need to look snappy


# ─── Result envelope ─────────────────────────────────────────────


@dataclass(frozen=True)
class MtResult:
    """What every admin-client method returns.

    The UI checks `ok`; if False it renders the error text inline.
    Routes serialize this to JSON as `{ok, data, error, took_ms,
    cached, dialed_address, mode}`.
    """
    ok: bool
    data: Any = None
    error: str = ""
    took_ms: int = 0
    cached: bool = False
    dialed_address: str = ""
    mode: str = "direct"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "took_ms": self.took_ms,
            "cached": self.cached,
            "dialed_address": self.dialed_address,
            "mode": self.mode,
        }


# ─── Cache ───────────────────────────────────────────────────────


@dataclass
class _CacheEntry:
    fetched_at: float = 0.0
    value: Any = None


class _TTLCache:
    """Per-process keyed cache. Keys are (router_id, operation)."""

    def __init__(self) -> None:
        self._entries: dict[tuple[int, str], _CacheEntry] = {}
        self._lock = threading.Lock()

    def get_or_fetch(
        self,
        *,
        router_id: int,
        operation: str,
        ttl_sec: float,
        fetcher: Callable[[], T],
    ) -> tuple[T, bool]:
        """Return (value, was_cached)."""
        key = (router_id, operation)
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry and now - entry.fetched_at <= ttl_sec:
                return entry.value, True
        value = fetcher()
        with self._lock:
            self._entries[key] = _CacheEntry(now, value)
        return value, False

    def invalidate(self, router_id: int, operation: Optional[str] = None) -> None:
        """Drop cached value(s) — call after a mutation."""
        with self._lock:
            if operation is None:
                self._entries = {
                    k: v for k, v in self._entries.items()
                    if k[0] != router_id
                }
            else:
                self._entries.pop((router_id, operation), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = _TTLCache()


def invalidate_cache(router_id: int, operation: Optional[str] = None) -> None:
    """Public hook for mutation endpoints (K8 reboot, K5 disconnect)."""
    _cache.invalidate(router_id, operation)


# ─── Client wrapper ─────────────────────────────────────────────


def _build_router_cfg(nas: Mapping[str, Any]) -> dict:
    """Turn a `nas_devices` row into the `router_cfg` dict the
    existing pool expects. Address comes from the resolver, NOT
    from `nas['address']` directly."""
    host = resolve_connection_address(nas)
    return {
        "id": int(nas.get("id") or 0),
        "host": host,
        "port": int(nas.get("api_port") or 8728),
        "username": str(nas.get("api_user") or ""),
        "password": str(nas.get("api_password") or ""),
        "use_tls": bool(nas.get("api_use_tls") or 0),
        "verify_tls": True,
        "timeout_sec": int(nas.get("api_timeout_sec") or 10),
    }


def _safe_dial(
    *,
    nas: Mapping[str, Any],
    operation: str,
    work: Callable,
) -> MtResult:
    """Run `work(client)` inside the pool, normalise every error
    to an `MtResult(ok=False)` so route handlers never see a
    socket / auth / trap exception."""
    descriptor = resolve_connection_descriptor(nas)
    router_id = int(nas.get("id") or 0)
    started = time.perf_counter()

    if not descriptor["address"]:
        return MtResult(
            ok=False,
            error="عنوان الراوتر غير محدد",
            took_ms=0,
            dialed_address="",
            mode=descriptor["mode"],
        )

    cfg = _build_router_cfg(nas)
    try:
        with _pool_acquire(cfg) as client:
            data = work(client)
    except AuthError as exc:
        _LOG.warning(
            "MT %s: auth failure router=%d address=%s — %s",
            operation, router_id, descriptor["address"], exc,
        )
        return MtResult(
            ok=False,
            error=f"فشل تسجيل الدخول: {exc}",
            took_ms=int((time.perf_counter() - started) * 1000),
            dialed_address=descriptor["address"],
            mode=descriptor["mode"],
        )
    except ConnectError as exc:
        _LOG.warning(
            "MT %s: connect failure router=%d address=%s — %s",
            operation, router_id, descriptor["address"], exc,
        )
        return MtResult(
            ok=False,
            error=f"تعذر الاتصال: {exc}",
            took_ms=int((time.perf_counter() - started) * 1000),
            dialed_address=descriptor["address"],
            mode=descriptor["mode"],
        )
    except MikrotikTrap as exc:
        return MtResult(
            ok=False,
            error=f"رفض الراوتر العملية: {exc}",
            took_ms=int((time.perf_counter() - started) * 1000),
            dialed_address=descriptor["address"],
            mode=descriptor["mode"],
        )
    except (MikrotikError, OSError) as exc:
        return MtResult(
            ok=False,
            error=f"خطأ في الاتصال: {exc}",
            took_ms=int((time.perf_counter() - started) * 1000),
            dialed_address=descriptor["address"],
            mode=descriptor["mode"],
        )

    return MtResult(
        ok=True,
        data=data,
        took_ms=int((time.perf_counter() - started) * 1000),
        dialed_address=descriptor["address"],
        mode=descriptor["mode"],
    )


def fetch_cached(
    *,
    nas: Mapping[str, Any],
    operation: str,
    ttl_sec: float,
    work: Callable,
) -> MtResult:
    """Cached variant. The first call hits the router, subsequent
    calls within `ttl_sec` return the cached `MtResult` with
    `cached=True` so the UI can flag it.

    Errors are NOT cached — if the router is down we keep retrying
    until it comes back (with the natural backoff that comes from
    the operator only refreshing every few seconds anyway)."""
    router_id = int(nas.get("id") or 0)

    def _fetch() -> MtResult:
        return _safe_dial(nas=nas, operation=operation, work=work)

    cached_value, was_cached = _cache.get_or_fetch(
        router_id=router_id,
        operation=operation,
        ttl_sec=ttl_sec,
        fetcher=_fetch,
    )

    # Don't cache failures — re-fetch next time so the UI clears
    # quickly when the router comes back up.
    if was_cached and isinstance(cached_value, MtResult) and not cached_value.ok:
        _cache.invalidate(router_id, operation)
        cached_value = _fetch()
        was_cached = False

    if isinstance(cached_value, MtResult):
        # Preserve the original `took_ms` but mark it cached when relevant.
        return MtResult(
            ok=cached_value.ok,
            data=cached_value.data,
            error=cached_value.error,
            took_ms=cached_value.took_ms,
            cached=was_cached,
            dialed_address=cached_value.dialed_address,
            mode=cached_value.mode,
        )
    # Defensive — shouldn't happen, but tolerate a misuse.
    return MtResult(
        ok=False,
        error="invalid cached value",
        cached=was_cached,
    )


# ─── Convenience: a fetch helper for /system/* style endpoints ──


def system_resource(nas: Mapping[str, Any]) -> MtResult:
    return fetch_cached(
        nas=nas,
        operation="system/resource",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/system/resource/print")),
    )


def system_health(nas: Mapping[str, Any]) -> MtResult:
    return fetch_cached(
        nas=nas,
        operation="system/health",
        ttl_sec=TTL_HEALTH,
        work=lambda c: list(c.print_("/system/health/print")),
    )


def system_identity(nas: Mapping[str, Any]) -> MtResult:
    return fetch_cached(
        nas=nas,
        operation="system/identity",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/system/identity/print")),
    )


def system_clock(nas: Mapping[str, Any]) -> MtResult:
    # Time changes every second; serve from short cache only.
    return fetch_cached(
        nas=nas,
        operation="system/clock",
        ttl_sec=5.0,
        work=lambda c: list(c.print_("/system/clock/print")),
    )


def system_routerboard(nas: Mapping[str, Any]) -> MtResult:
    return fetch_cached(
        nas=nas,
        operation="system/routerboard",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/system/routerboard/print")),
    )


# ─── K4: interfaces + network fetchers ───────────────────────────


def interface_list(nas: Mapping[str, Any]) -> MtResult:
    """`/interface/print` — every interface with rx/tx-byte counters,
    MAC, type, running flag. The dashboard renders this as a table
    and the SSE stream (K4.2) uses one of the names from it."""
    return fetch_cached(
        nas=nas,
        operation="interface/list",
        ttl_sec=TTL_INTERFACES,
        work=lambda c: list(c.print_("/interface/print")),
    )


def interface_traffic(nas: Mapping[str, Any], name: str) -> MtResult:
    """`/interface/monitor-traffic once=` — one snapshot of
    rx/tx-bits-per-second + packets-per-second for a named iface.

    Cache TTL is short (≈ TTL_INTERFACES) so two quick polls don't
    pin the CPU, but a 5-second refresh cadence still feels live.

    `name` is passed straight to the router as the `=interface=`
    attribute; the wire client escapes it. We still strip empty /
    whitespace input client-side so a missing path-segment surfaces
    as a clean error envelope rather than a router trap.
    """
    iface = (name or "").strip()
    if not iface:
        return MtResult(ok=False, error="اسم الواجهة غير محدد")

    def work(client):
        rows = client.run(
            "/interface/monitor-traffic",
            attrs={"interface": iface, "once": ""},
        )
        return [s["attrs"] for s in rows if s.get("reply") == "!re"]

    return fetch_cached(
        nas=nas,
        operation=f"interface/traffic:{iface}",
        ttl_sec=TTL_INTERFACES,
        work=work,
    )


def ip_addresses(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/address/print` — address ↔ interface map."""
    return fetch_cached(
        nas=nas,
        operation="ip/addresses",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/address/print")),
    )


def ip_routes(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/route/print` — routing table snapshot."""
    return fetch_cached(
        nas=nas,
        operation="ip/routes",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/route/print")),
    )


# ─── K5: hotspot + PPP active users ──────────────────────────────


def hotspot_active(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/hotspot/active/print` — every live hotspot session.

    Rows carry `.id` (used by the K5.2 disconnect mutation), `user`,
    `address`, `mac-address`, `uptime`, `bytes-in`, `bytes-out`.
    """
    return fetch_cached(
        nas=nas,
        operation="hotspot/active",
        ttl_sec=TTL_ACTIVE_USERS,
        work=lambda c: list(c.print_("/ip/hotspot/active/print")),
    )


def ppp_active(nas: Mapping[str, Any]) -> MtResult:
    """`/ppp/active/print` — every live PPPoE / PPTP / L2TP session."""
    return fetch_cached(
        nas=nas,
        operation="ppp/active",
        ttl_sec=TTL_ACTIVE_USERS,
        work=lambda c: list(c.print_("/ppp/active/print")),
    )


# ─── K6: simple queues + firewall ────────────────────────────────


def queue_simple_list(nas: Mapping[str, Any]) -> MtResult:
    """`/queue/simple/print` — list of simple queues with limits."""
    return fetch_cached(
        nas=nas,
        operation="queue/simple",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/queue/simple/print")),
    )


# Fields the admin UI may edit on a simple queue. Anything else is
# refused — we don't blind-edit parent/target/type because those
# would silently break the queue rather than just adjust a limit.
QUEUE_SIMPLE_EDITABLE = frozenset({"max-limit", "disabled", "comment"})


def _coerce_queue_attr(key: str, value: Any) -> str:
    """Stringify an attr value for the wire client, with shape
    checks for the few keys we accept."""
    if key == "disabled":
        # RouterOS expects 'yes'/'no'.
        if isinstance(value, bool):
            return "yes" if value else "no"
        text = str(value).strip().lower()
        if text in {"yes", "true", "1"}:
            return "yes"
        if text in {"no", "false", "0"}:
            return "no"
        raise ValueError("disabled يجب أن يكون true/false")
    if value is None:
        return ""
    return str(value)


def queue_simple_set(
    nas: Mapping[str, Any], queue_id: str, attrs: Mapping[str, Any],
) -> MtResult:
    """`/queue/simple/set .id=<id> ...` — edit a queue's limits.

    Only `max-limit`, `disabled`, `comment` may be set. Bad input
    short-circuits to a clean error envelope; a router-side trap
    surfaces as one too.
    """
    qid = (queue_id or "").strip()
    if not qid:
        return MtResult(ok=False, error="معرّف الطابور غير محدد")
    if not attrs:
        return MtResult(ok=False, error="لا توجد حقول للتحديث")

    rejected = [k for k in attrs if k not in QUEUE_SIMPLE_EDITABLE]
    if rejected:
        return MtResult(
            ok=False,
            error=f"حقول غير مسموح بتعديلها: {', '.join(rejected)}",
        )

    try:
        wire_attrs = {k: _coerce_queue_attr(k, v) for k, v in attrs.items()}
    except ValueError as exc:
        return MtResult(ok=False, error=str(exc))
    wire_attrs[".id"] = qid

    return _run_mutation(
        nas,
        operation="queue/simple/set",
        work=lambda c: c.run("/queue/simple/set", attrs=wire_attrs),
        invalidate=("queue/simple",),
    )


def _run_mutation(
    nas: Mapping[str, Any],
    *,
    operation: str,
    work: Callable,
    invalidate: Iterable[str] = (),
) -> MtResult:
    """Common scaffold for write operations.

    Always bypasses the cache (mutation), then on success drops the
    listed cache slots so the next read reflects the new state.
    Failures leave the cache alone — last-known-good list is more
    useful to the operator than an empty one."""
    result = _safe_dial(nas=nas, operation=operation, work=work)
    if result.ok:
        router_id = int(nas.get("id") or 0)
        for op in invalidate:
            _cache.invalidate(router_id, op)
    return result


def disconnect_hotspot_session(
    nas: Mapping[str, Any], session_id: str,
) -> MtResult:
    """`/ip/hotspot/active/remove .id=<sid>` — kick a hotspot user.

    The `.id` is the value from a row returned by `hotspot_active`.
    Invalidates the active-list cache on success so the UI refresh
    immediately reflects the kick. Audit logging is the route
    layer's job (it has the actor context).
    """
    sid = (session_id or "").strip()
    if not sid:
        return MtResult(ok=False, error="معرّف الجلسة غير محدد")
    return _run_mutation(
        nas,
        operation="hotspot/active/remove",
        work=lambda c: c.run(
            "/ip/hotspot/active/remove", attrs={".id": sid},
        ),
        invalidate=("hotspot/active",),
    )


def disconnect_ppp_session(
    nas: Mapping[str, Any], session_id: str,
) -> MtResult:
    """`/ppp/active/remove .id=<sid>` — kick a PPPoE / PPTP / L2TP
    session. Same shape as the hotspot variant."""
    sid = (session_id or "").strip()
    if not sid:
        return MtResult(ok=False, error="معرّف الجلسة غير محدد")
    return _run_mutation(
        nas,
        operation="ppp/active/remove",
        work=lambda c: c.run(
            "/ppp/active/remove", attrs={".id": sid},
        ),
        invalidate=("ppp/active",),
    )


# Default upper bound on SSE samples — 150 × 2 s ≈ 5 min, after
# which the browser's EventSource auto-reconnects.
SSE_DEFAULT_MAX_SAMPLES = 150
SSE_DEFAULT_PERIOD_SEC = 2.0


def stream_interface_samples(
    nas: Mapping[str, Any],
    name: str,
    *,
    period_sec: float = SSE_DEFAULT_PERIOD_SEC,
    max_samples: int = SSE_DEFAULT_MAX_SAMPLES,
    _sleep: Callable[[float], None] = time.sleep,
):
    """Yield live `MtResult` snapshots of one interface's traffic.

    Bypasses the TTL cache on purpose — the consumer (Server-Sent
    Events stream) wants fresh samples every `period_sec`. If a
    sample fails (router went away mid-stream), we yield the error
    envelope and stop; the EventSource client will reconnect on
    its own schedule and we don't pin a worker hammering a dead
    router.

    Cooperative — yields one sample, then sleeps. Closing the
    generator (client disconnects) breaks the loop on the next
    yield.
    """
    iface = (name or "").strip()
    if not iface:
        yield MtResult(ok=False, error="اسم الواجهة غير محدد")
        return

    def _work(client):
        rows = client.run(
            "/interface/monitor-traffic",
            attrs={"interface": iface, "once": ""},
        )
        return [s["attrs"] for s in rows if s.get("reply") == "!re"]

    bound = max(1, int(max_samples))
    for i in range(bound):
        result = _safe_dial(
            nas=nas,
            operation=f"interface/sse:{iface}",
            work=_work,
        )
        yield result
        if not result.ok:
            return
        if i == bound - 1:
            return
        _sleep(period_sec)


__all__ = [
    "MtResult",
    "TTL_SYSTEM",
    "TTL_HEALTH",
    "TTL_INTERFACES",
    "TTL_ACTIVE_USERS",
    "fetch_cached",
    "invalidate_cache",
    "system_resource",
    "system_health",
    "system_identity",
    "system_clock",
    "system_routerboard",
    "interface_list",
    "interface_traffic",
    "ip_addresses",
    "ip_routes",
    "hotspot_active",
    "ppp_active",
    "disconnect_hotspot_session",
    "disconnect_ppp_session",
    "queue_simple_list",
    "queue_simple_set",
    "QUEUE_SIMPLE_EDITABLE",
    "stream_interface_samples",
    "SSE_DEFAULT_MAX_SAMPLES",
    "SSE_DEFAULT_PERIOD_SEC",
]
