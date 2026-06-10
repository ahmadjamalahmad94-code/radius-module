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
import re
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
        if isinstance(value, MtResult) and not value.ok:
            return value, False
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
        # Default 3s — short enough to keep the dashboard snappy when
        # a router is unreachable, long enough for a healthy router
        # to respond inside one round-trip. Per-NAS override via the
        # `api_timeout_sec` column when set > 0.
        "timeout_sec": int(nas.get("api_timeout_sec") or 3),
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

    except Exception as exc:  # pragma: no cover - last-resort router boundary
        exc_name = exc.__class__.__name__
        if exc_name == "AuthError":
            return MtResult(
                ok=False,
                error=f"فشل تسجيل الدخول: {exc}",
                took_ms=int((time.perf_counter() - started) * 1000),
                dialed_address=descriptor["address"],
                mode=descriptor["mode"],
            )
        if exc_name == "ConnectError":
            return MtResult(
                ok=False,
                error=f"تعذر الاتصال: {exc}",
                took_ms=int((time.perf_counter() - started) * 1000),
                dialed_address=descriptor["address"],
                mode=descriptor["mode"],
            )
        if exc_name == "MikrotikTrap":
            return MtResult(
                ok=False,
                error=f"رفض الراوتر العملية: {exc}",
                took_ms=int((time.perf_counter() - started) * 1000),
                dialed_address=descriptor["address"],
                mode=descriptor["mode"],
            )
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
    """Fetch /system/clock/print and normalise the row so the UI
    always sees the same two keys regardless of RouterOS version.

    RouterOS 7 returns: time, date, time-zone-name, gmt-offset, …
    Some 6.x flavours used current-time / current-date. A few
    builds attach the time fields directly to the response prop
    list (no separate row). We flatten all of those into a single
    {time, date, timezone} row so the frontend can stop guessing.

    Time changes every second; short cache only.
    """
    def _work(c):
        rows = list(c.print_("/system/clock/print"))
        if not rows:
            return rows
        row = rows[0] or {}
        # Accept either field shape, fall through to whatever's present.
        time_val = (row.get("time")
                    or row.get("current-time")
                    or row.get("currentTime")
                    or "")
        date_val = (row.get("date")
                    or row.get("current-date")
                    or row.get("currentDate")
                    or "")
        tz_val = (row.get("time-zone-name")
                  or row.get("timeZoneName")
                  or row.get("time-zone")
                  or "")
        # Return BOTH the canonical normalised keys AND the original
        # row keys, so future debug or downstream consumers can read
        # the raw values if they need them.
        normalised = dict(row)
        normalised["time"] = str(time_val)
        normalised["date"] = str(date_val)
        normalised["timezone"] = str(tz_val)
        return [normalised]

    return fetch_cached(
        nas=nas,
        operation="system/clock",
        ttl_sec=5.0,
        work=_work,
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
    and the SSE stream (K4.2) uses one of the names from it.

    Enriched with /interface/ethernet/print so ethernet rows pick up
    the negotiated `rate` (10Mbps / 100Mbps / 1Gbps) +
    `auto-negotiation` + `full-duplex` — that's what shows up in the
    dashboard's «نوع الاتصال» column. Non-ethernet rows (VLANs,
    bridges, PPPoE, …) just don't get those keys, which the
    frontend renders as «—».
    """
    def _work(c):
        ifaces = list(c.print_("/interface/print"))

        # Names of ethernet interfaces — feed them to monitor once=
        # in one round-trip so we get rate/auto-neg/full-duplex per
        # name. `/interface/ethernet/print` only shows the CONFIGURED
        # bandwidth, not the negotiated link rate — operator's
        # «Auto Negotiation: done, Rate: 100Mbps» comes from MONITOR.
        eth_names = [
            (i or {}).get("name") for i in ifaces
            if (i or {}).get("type") == "ether"
        ]
        eth_names = [n for n in eth_names if n]
        eth_status_by_name: dict[str, dict] = {}
        if eth_names:
            try:
                # Single call, comma-joined names — RouterOS returns
                # one !re per name with its current rate/status.
                rows = c.run(
                    "/interface/ethernet/monitor",
                    attrs={
                        "numbers": ",".join(eth_names),
                        "once": "",
                    },
                )
                for s in rows:
                    if s.get("reply") != "!re":
                        continue
                    a = s.get("attrs") or {}
                    name = a.get("name")
                    if name:
                        eth_status_by_name[name] = a
            except Exception:  # noqa: BLE001
                # Soft-fail — the dashboard tolerates missing `rate`.
                pass

        for iface in ifaces:
            name = (iface or {}).get("name")
            eth = eth_status_by_name.get(name) if name else None
            if eth:
                # `rate` is the negotiated link rate (100Mbps / 1Gbps).
                # `status` is link-ok / no-link.
                # `auto-negotiation` is done / incomplete.
                for key in ("rate", "auto-negotiation",
                            "full-duplex", "status"):
                    if eth.get(key) and not iface.get(key):
                        iface[key] = eth[key]
        return ifaces

    return fetch_cached(
        nas=nas,
        operation="interface/list",
        ttl_sec=TTL_INTERFACES,
        work=_work,
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


def ip_neighbors(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/neighbor/print` — devices discovered via MNDP/CDP/LLDP.

    Each row carries identity, MAC, IPv4/IPv6, interface, version,
    platform, board, and uptime. RouterOS rebuilds this list on
    every discovery beacon (~ once per minute on most hardware),
    so caching for TTL_SYSTEM is safe and keeps the page snappy.
    """
    return fetch_cached(
        nas=nas,
        operation="ip/neighbors",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/neighbor/print")),
    )


def dhcp_client_list(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/dhcp-client/print` — كل عملاء DHCP على الراوتر مع حالتهم.

    تستخدمه خدمة كشف اللوب (port_script_services): العملاء الموسومون
    HR-LoopDetect يُقرأ منهم `status` (bound = استلم عنوانًا = لوب /
    searching = لا لوب)، و`address` / `gateway` / `dhcp-server` الراجعة.
    مهلة تخزين قصيرة (≈5s) لأن الحالة يجب أن تبقى حيّة عند الضغط على زر
    «فحص اللوب» دون أن نهاجم الراوتر عند نقرتين متتاليتين."""
    return fetch_cached(
        nas=nas,
        operation="ip/dhcp-client",
        ttl_sec=5.0,
        work=lambda c: list(c.print_("/ip/dhcp-client/print")),
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


def firewall_filter(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/firewall/filter/print` — filter chain. Read-only by
    design (per the plan): editing filter rules blind through an
    API is too easy to lock yourself out with."""
    return fetch_cached(
        nas=nas,
        operation="firewall/filter",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/firewall/filter/print")),
    )


def firewall_nat(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/firewall/nat/print` — NAT rules. Read-only."""
    return fetch_cached(
        nas=nas,
        operation="firewall/nat",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/firewall/nat/print")),
    )


def address_list_list(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/firewall/address-list/print` — address-list entries.

    The list is small and stable enough to share one cache bucket
    across every list-name; the UI does the per-list filtering.
    """
    return fetch_cached(
        nas=nas,
        operation="firewall/address-list",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/firewall/address-list/print")),
    )


def address_list_add(
    nas: Mapping[str, Any], *, list_name: str, address: str,
    comment: str = "", timeout: str = "",
) -> MtResult:
    """`/ip/firewall/address-list/add list=<L> address=<A> ...`.

    `address` may be a single IP, CIDR, or RouterOS-style range —
    we don't validate here, the router will reject malformed input
    and that surfaces as a clean MtResult error.
    """
    name = (list_name or "").strip()
    addr = (address or "").strip()
    if not name:
        return MtResult(ok=False, error="اسم القائمة غير محدد")
    if not addr:
        return MtResult(ok=False, error="العنوان غير محدد")
    attrs: dict = {"list": name, "address": addr}
    if comment:
        attrs["comment"] = str(comment)
    if timeout:
        attrs["timeout"] = str(timeout)
    return _run_mutation(
        nas,
        operation="firewall/address-list/add",
        work=lambda c: c.run("/ip/firewall/address-list/add", attrs=attrs),
        invalidate=("firewall/address-list",),
    )


def address_list_remove(
    nas: Mapping[str, Any], entry_id: str,
) -> MtResult:
    """`/ip/firewall/address-list/remove .id=<id>`."""
    eid = (entry_id or "").strip()
    if not eid:
        return MtResult(ok=False, error="معرّف المدخل غير محدد")
    return _run_mutation(
        nas,
        operation="firewall/address-list/remove",
        work=lambda c: c.run(
            "/ip/firewall/address-list/remove", attrs={".id": eid},
        ),
        invalidate=("firewall/address-list",),
    )


# ─── K7: logs + diagnostics ──────────────────────────────────────


TTL_LOG = 5.0  # log tail is interesting fresh — short cache only


def log_tail(
    nas: Mapping[str, Any],
    *,
    topics: Optional[Iterable[str]] = None,
    limit: int = 100,
) -> MtResult:
    """`/log/print` with optional client-side topic filter + tail.

    RouterOS returns the whole log buffer; the row's `topics` field
    is a comma-separated string like 'system,info,account'. We
    match if ANY of the requested topics appears in that field.
    Filtering after the fetch keeps the router-side query simple
    and lets the cache hold one entry per (topics, limit) bucket.
    """
    wanted = tuple(sorted({t.strip().lower() for t in (topics or []) if t}))
    bound = max(1, min(int(limit or 100), 1000))

    def work(client):
        rows = list(client.print_("/log/print"))
        if wanted:
            kept = []
            for row in rows:
                row_topics = str(row.get("topics") or "").lower()
                if any(t in row_topics for t in wanted):
                    kept.append(row)
            rows = kept
        return rows[-bound:]

    cache_key = f"log/tail::{','.join(wanted)}::{bound}"
    return fetch_cached(
        nas=nas, operation=cache_key, ttl_sec=TTL_LOG, work=work,
    )


# Hard upper bounds — operator should NOT be able to flood the
# router by asking for 10 000-packet pings or 100-hop traces.
PING_MAX_COUNT = 20
TRACEROUTE_MAX_COUNT = 5


def _run_diagnostic(
    nas: Mapping[str, Any], *, operation: str, work: Callable,
) -> MtResult:
    """Diagnostics (ping / traceroute / resolve) bypass the cache —
    the whole point of running one is to get a fresh answer. Shape
    is otherwise identical to a cached read."""
    return _safe_dial(nas=nas, operation=operation, work=work)


def tool_ping(
    nas: Mapping[str, Any], *, target: str, count: int = 4,
) -> MtResult:
    """`/ping address=<t> count=<n>` — synchronous; returns one row
    per packet plus a summary row."""
    addr = (target or "").strip()
    if not addr:
        return MtResult(ok=False, error="عنوان الهدف غير محدد")
    n = max(1, min(int(count or 1), PING_MAX_COUNT))

    def work(client):
        rows = client.run("/ping", attrs={"address": addr, "count": str(n)})
        return [s["attrs"] for s in rows if s.get("reply") == "!re"]

    return _run_diagnostic(nas, operation=f"tool/ping:{addr}", work=work)


def tool_traceroute(
    nas: Mapping[str, Any], *, target: str, count: int = 1,
) -> MtResult:
    """`/tool/traceroute address=<t> count=<n>` — one row per hop."""
    addr = (target or "").strip()
    if not addr:
        return MtResult(ok=False, error="عنوان الهدف غير محدد")
    n = max(1, min(int(count or 1), TRACEROUTE_MAX_COUNT))

    def work(client):
        rows = client.run(
            "/tool/traceroute",
            attrs={"address": addr, "count": str(n)},
        )
        return [s["attrs"] for s in rows if s.get("reply") == "!re"]

    return _run_diagnostic(
        nas, operation=f"tool/traceroute:{addr}", work=work,
    )


def tool_dns_resolve(
    nas: Mapping[str, Any], *, name: str, server: str = "",
) -> MtResult:
    """`/resolve name=<n> [server=<s>]` — RouterOS 7+ resolver.

    Field-quirk worth remembering: in some RouterOS 7 revisions
    `/resolve` puts the answer in the !done reply attrs, not in a
    !re row. The previous version of this helper filtered for !re
    only and lost every answer. We now harvest any reply (!re,
    !done, !trap) that carries an address-like attribute — that
    covers all observed shapes.

    We also synthesise an `addresses[]` list from `address` and the
    comma-separated `address-list` field so the UI doesn't have to
    parse the comma string.
    """
    target = (name or "").strip()
    if not target:
        return MtResult(ok=False, error="اسم النطاق غير محدد")
    attrs: dict = {"name": target}
    srv = (server or "").strip()
    if srv:
        attrs["server"] = srv

    def work(client):
        rows = client.run("/resolve", attrs=attrs)
        out = []
        all_addrs: list[str] = []
        for s in rows or []:
            a = (s.get("attrs") or {}) if isinstance(s, dict) else {}
            if not a:
                continue
            # Any reply with an address-like attr counts as an answer.
            addr     = (a.get("address") or "").strip()
            addr6    = (a.get("ipv6") or a.get("address6") or "").strip()
            addr_csv = (a.get("address-list") or "").strip()
            if not (addr or addr6 or addr_csv):
                # Skip pure status replies (no address attached).
                continue
            row = dict(a)
            row.setdefault("name", target)
            out.append(row)
            if addr:    all_addrs.append(addr)
            if addr6:   all_addrs.append(addr6)
            if addr_csv:
                for piece in addr_csv.split(","):
                    p = piece.strip()
                    if p and p not in all_addrs:
                        all_addrs.append(p)
        if all_addrs:
            # Inject a synthesized aggregator row so the UI's
            # `data.addresses` / `data.data[].address` lookups both
            # succeed even if RouterOS only ever returned a single
            # !done reply.
            agg = {"name": target, "address": all_addrs[0],
                   "addresses": all_addrs}
            # Put aggregator first so the renderer's "first row"
            # shortcut hits it.
            out.insert(0, agg)
        return out

    return _run_diagnostic(
        nas, operation=f"tool/resolve:{target}", work=work,
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


# ─── K8: backup + reboot + identity ──────────────────────────────


# Names that are obviously bad. RouterOS itself will reject things
# we don't catch here — this layer just rejects the dangerous
# patterns (path traversal, control chars, empty) before they ever
# hit the wire.
_BACKUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _sanitize_backup_name(raw: str) -> str:
    """Return the cleaned backup name, or raise ValueError with an
    Arabic message. Strips whitespace; rejects empty, path
    traversal, control chars, leading dot, anything outside the
    `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` allowlist."""
    name = (raw or "").strip()
    if not name:
        raise ValueError("اسم النسخة مطلوب")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("اسم النسخة يحتوي على رموز ممنوعة")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("اسم النسخة يحتوي على رموز تحكم")
    if not _BACKUP_NAME_RE.match(name):
        raise ValueError(
            "اسم النسخة يجب أن يبدأ بحرف/رقم ويحوي [A-Za-z0-9._-] فقط"
        )
    return name


_IDENTITY_MAX_LEN = 32
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9._\-]{1,32}$")


def _sanitize_identity(raw: str) -> str:
    """RouterOS identity name. Letters/digits/dot/dash/underscore,
    1-32 chars. Rejects control chars + spaces + path-traversal-
    ish input. The router would reject some of these too; rejecting
    here gives the operator an Arabic message instead of a trap."""
    name = (raw or "").strip()
    if not name:
        raise ValueError("اسم الراوتر مطلوب")
    if len(name) > _IDENTITY_MAX_LEN:
        raise ValueError(f"اسم الراوتر أطول من {_IDENTITY_MAX_LEN} حرفًا")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("اسم الراوتر يحتوي على رموز تحكم")
    if not _IDENTITY_RE.match(name):
        raise ValueError(
            "اسم الراوتر يجب أن يحوي [A-Za-z0-9._-] فقط"
        )
    return name


def system_reboot(nas: Mapping[str, Any]) -> MtResult:
    """`/system/reboot` — kicks the router. The router goes away
    for a minute, so every system/* cache slot is dropped: the UI
    will re-discover an offline router rather than serve stale
    uptime / health figures."""
    return _run_mutation(
        nas,
        operation="system/reboot",
        work=lambda c: c.run("/system/reboot"),
        invalidate=(
            "system/resource", "system/health", "system/identity",
            "system/clock", "system/routerboard",
        ),
    )


def system_ntp_sync(nas: Mapping[str, Any]) -> MtResult:
    """Force a fresh NTP sync. RouterOS has no single «sync now»
    verb — instead we toggle the NTP client off+on, which retriggers
    the initial poll against the configured server pool. If the
    client wasn't enabled to begin with, this still leaves it
    enabled afterwards (intentional: the operator clearly wants the
    clock synced).

    Returns the post-toggle /system/clock + /system/ntp/client/print
    rows so the operator can confirm the new time without a refresh.
    """
    def _work(c):
        # Step 1: disable (idempotent), step 2: re-enable. Each call
        # is wire-cheap; the gap forces the client to re-bind to its
        # pool peers on the next packet.
        try:
            c.run("/system/ntp/client/set", attrs={"enabled": "no"})
        except Exception:  # noqa: BLE001
            # Some RouterOS revisions structure NTP under
            # /system/ntp/client/set, others under /ip/ntp/client.
            # Swallow disable-side failures and let enable be the
            # authoritative step.
            pass
        c.run("/system/ntp/client/set", attrs={"enabled": "yes"})
        # Best-effort: include the current clock + client state so
        # the UI shows the new time without polling. If either lookup
        # fails (older revisions returned them under different
        # paths), an empty dict is fine — the friendly card still
        # renders the success header.
        clock = []
        client_state = []
        try:
            clock = list(c.run("/system/clock/print"))
        except Exception:  # noqa: BLE001
            pass
        try:
            client_state = list(c.run("/system/ntp/client/print"))
        except Exception:  # noqa: BLE001
            pass
        out = {}
        if clock:
            out["time"] = (clock[0].get("time") or "")
            out["date"] = (clock[0].get("date") or "")
        if client_state:
            cs = client_state[0]
            out["ntp_peer"] = (cs.get("servers")
                               or cs.get("primary-ntp")
                               or "")
            out["status"] = cs.get("status") or ""
        return out

    return _run_mutation(
        nas,
        operation="system/ntp/sync",
        work=_work,
        invalidate=("system/clock", "system/resource"),
    )


def ip_dns_cache_flush(nas: Mapping[str, Any]) -> MtResult:
    """`/ip/dns/cache/flush` — clears the resolver cache. Useful
    after changing upstream DNS or after a customer reports stale
    resolutions. Non-destructive: future lookups simply re-fetch
    from the configured server pool."""
    def _work(c):
        c.run("/ip/dns/cache/flush")
        return {"flushed": True}

    return _run_mutation(
        nas,
        operation="ip/dns/cache/flush",
        work=_work,
        invalidate=(),
    )


def system_identity_set(
    nas: Mapping[str, Any], *, name: str,
) -> MtResult:
    """`/system/identity/set name=<n>` — rename the router. Drops
    the cached identity so the new name shows up on the next
    refresh."""
    try:
        clean = _sanitize_identity(name)
    except ValueError as exc:
        return MtResult(ok=False, error=str(exc))
    return _run_mutation(
        nas,
        operation="system/identity/set",
        work=lambda c: c.run(
            "/system/identity/set", attrs={"name": clean},
        ),
        invalidate=("system/identity",),
    )


class FileDownloadNotSupported(NotImplementedError):
    """Raised when the router cannot be addressed for a file download
    at all (no resolvable address). Distinct exception type so the
    route + tests can target it without catching unrelated errors."""


class FileDownloadError(RuntimeError):
    """Raised when a real FTP download attempt fails — auth refused,
    file missing/forbidden, or the transfer broke mid-stream. The
    route layer turns this into a 502 envelope (the router was
    reachable but the download itself failed)."""


# K8.1b — real binary file download from the router over FTP.
#
# The RouterOS API is text-only and won't stream large binary
# `contents=` payloads safely, so we use the router's built-in FTP
# service instead (RouterOS shares one user database across API/FTP/
# SSH — the same `api_user` works as long as that user's group has
# the `ftp` policy). We RETR the file into a spooled buffer, then
# hand the route an iterator of byte chunks plus a Content-Length
# hint so the browser gets a real download with a progress bar.
FTP_TIMEOUT_SEC = 20
FTP_CHUNK_BYTES = 64 * 1024
_FTP_SPOOL_MAX = 8 * 1024 * 1024  # keep ≤8MB in RAM, spill bigger to disk


def _ftp_connect(host: str, port: int, username: str, password: str, timeout: int):
    """Open + authenticate an FTP control connection. Isolated so
    tests can monkeypatch it with a fake transport."""
    import ftplib

    ftp = ftplib.FTP()
    ftp.connect(host, port or 21, timeout=timeout)
    ftp.login(username or "anonymous", password or "")
    return ftp


def file_download_stream(
    nas: Mapping[str, Any], filename: str, *, chunk_size: int = FTP_CHUNK_BYTES,
):
    """Download `filename` from the router over FTP.

    Returns ``(size_bytes, iterator)`` where the iterator yields the
    file's bytes in `chunk_size` chunks. Raises
    `FileDownloadNotSupported` when the router has no resolvable
    address, or `FileDownloadError` when the FTP transfer itself
    fails. Never returns fabricated/empty bytes silently.
    """
    import ftplib
    import tempfile

    cfg = _build_router_cfg(nas)
    host = str(cfg.get("host") or "").strip()
    if not host:
        raise FileDownloadNotSupported("عنوان الراوتر غير محدد لتنزيل الملف.")
    ftp_port = int(nas.get("ftp_port") or 21)
    try:
        ftp = _ftp_connect(host, ftp_port, cfg.get("username", ""),
                           cfg.get("password", ""), FTP_TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001 - normalise every dial error
        raise FileDownloadError(
            f"تعذّر الاتصال بخدمة FTP على الراوتر ({host}:{ftp_port}). "
            f"تأكّد أن خدمة FTP مفعّلة وأن صلاحية المستخدم تتضمّن ftp. ({exc})"
        ) from exc

    buf = tempfile.SpooledTemporaryFile(max_size=_FTP_SPOOL_MAX)
    try:
        ftp.retrbinary(f"RETR {filename}", buf.write, blocksize=chunk_size)
    except ftplib.error_perm as exc:
        buf.close()
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass
        raise FileDownloadError(
            f"تعذّر تنزيل «{filename}» — الملف غير موجود أو الوصول مرفوض. ({exc})"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        buf.close()
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass
        raise FileDownloadError(f"انقطع تنزيل الملف من الراوتر. ({exc})") from exc
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass

    size = buf.tell()
    buf.seek(0)

    def _generate():
        try:
            while True:
                chunk = buf.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            buf.close()

    return size, _generate()


def file_list(nas: Mapping[str, Any]) -> MtResult:
    """`/file/print` — every file the router can see (backups,
    scripts, certs, etc.). Cached briefly so the dashboard listing
    doesn't hammer the router."""
    return fetch_cached(
        nas=nas,
        operation="file/list",
        ttl_sec=TTL_SYSTEM,
        work=lambda c: list(c.print_("/file/print")),
    )


def backup_save(
    nas: Mapping[str, Any], *, name: str,
) -> MtResult:
    """`/system/backup/save name=<n>` — creates `<n>.backup` on the
    router. We sanitize the name client-side; the router still
    rejects truly malformed inputs and that surfaces as a trap
    error in the envelope."""
    try:
        clean = _sanitize_backup_name(name)
    except ValueError as exc:
        return MtResult(ok=False, error=str(exc))
    return _run_mutation(
        nas,
        operation="system/backup/save",
        work=lambda c: c.run(
            "/system/backup/save", attrs={"name": clean},
        ),
        invalidate=("file/list",),
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
    "ip_neighbors",
    "dhcp_client_list",
    "hotspot_active",
    "ppp_active",
    "disconnect_hotspot_session",
    "disconnect_ppp_session",
    "queue_simple_list",
    "queue_simple_set",
    "QUEUE_SIMPLE_EDITABLE",
    "firewall_filter",
    "firewall_nat",
    "address_list_list",
    "address_list_add",
    "address_list_remove",
    "log_tail",
    "TTL_LOG",
    "tool_ping",
    "tool_traceroute",
    "tool_dns_resolve",
    "PING_MAX_COUNT",
    "TRACEROUTE_MAX_COUNT",
    "stream_interface_samples",
    "SSE_DEFAULT_MAX_SAMPLES",
    "SSE_DEFAULT_PERIOD_SEC",
    "file_list",
    "backup_save",
    "file_download_stream",
    "FileDownloadNotSupported",
    "system_reboot",
    "system_identity_set",
]
