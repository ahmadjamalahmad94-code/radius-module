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
from typing import Any, Callable, Mapping, Optional, TypeVar

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
]
