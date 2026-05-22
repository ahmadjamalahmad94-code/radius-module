"""O1 — per-router NAS counters.

Aggregates the headline numbers the Operations Center surfaces
per row: active hotspot session count, active PPP session count,
sum of interface byte counters, last successful API ping age.

The implementation reuses K3-K5 fetchers from
`mikrotik_admin_client` so the per-operation TTL cache covers
these too — the Operations Center can poll counters every 10s
without melting the routers.

Public surface:

    NasCounters(...)                     dataclass holding the
                                         aggregated numbers
    counters_for_nas(nas) -> MtResult    fetches + aggregates
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional

from . import mikrotik_admin_client as mac


@dataclass(frozen=True)
class NasCounters:
    """The five numbers each Operations Center row shows."""

    hotspot_active: int
    ppp_active: int
    rx_bytes_total: int
    tx_bytes_total: int
    last_seen_age_sec: Optional[int]   # None when no successful
                                        # API call yet
    fetched_at: float                    # epoch seconds


def _sum_bytes(rows: list[dict[str, Any]], key: str) -> int:
    """Sum a numeric field across every interface row. RouterOS
    returns these as decimal strings; missing / unparseable
    values count as 0 rather than blowing up the whole call."""
    total = 0
    for r in rows or []:
        raw = r.get(key)
        if raw is None:
            continue
        try:
            total += int(str(raw).strip())
        except (TypeError, ValueError):
            continue
    return total


def counters_for_nas(nas: Mapping[str, Any]) -> mac.MtResult:
    """Return a MtResult whose `data` is a NasCounters dict.

    Every sub-fetch goes through the standard cache+envelope path
    in `mikrotik_admin_client`, so a downstream `system_resource`
    poll an instant later re-uses the warm values. If any
    sub-fetch fails we still return what we managed to collect;
    the missing numbers come out as zeros (with the envelope-level
    `ok=False`) so the UI can show "partial data" cleanly.
    """
    hotspot_res = mac.hotspot_active(nas)
    ppp_res     = mac.ppp_active(nas)
    iface_res   = mac.interface_list(nas)

    # Pull the per-section data even if any sub-call failed —
    # an empty list is the right zero-state for counters.
    hot_rows = hotspot_res.data if hotspot_res.ok else []
    ppp_rows = ppp_res.data     if ppp_res.ok     else []
    if_rows  = iface_res.data   if iface_res.ok   else []

    counters = NasCounters(
        hotspot_active=len(hot_rows or []),
        ppp_active=len(ppp_rows or []),
        rx_bytes_total=_sum_bytes(if_rows, "rx-byte"),
        tx_bytes_total=_sum_bytes(if_rows, "tx-byte"),
        last_seen_age_sec=None,           # populated by routes layer
                                          # when it knows the cached
                                          # MtResult's timestamp.
        fetched_at=time.time(),
    )

    # Aggregate ok = all three sub-fetches succeeded. The route
    # layer can show a "stale" indicator when ok=False.
    aggregate_ok = hotspot_res.ok and ppp_res.ok and iface_res.ok
    # Combine errors so the UI has *something* to display when
    # things go sideways.
    parts: list[str] = []
    if not hotspot_res.ok and hotspot_res.error:
        parts.append(f"hotspot: {hotspot_res.error}")
    if not ppp_res.ok and ppp_res.error:
        parts.append(f"ppp: {ppp_res.error}")
    if not iface_res.ok and iface_res.error:
        parts.append(f"interfaces: {iface_res.error}")
    combined_error = "؛ ".join(parts)

    # Inherit dialed-address + mode from whichever sub-fetch
    # was attempted (they all share the same NAS row).
    dialed = (
        hotspot_res.dialed_address
        or ppp_res.dialed_address
        or iface_res.dialed_address
    )
    mode = (
        hotspot_res.mode
        or ppp_res.mode
        or iface_res.mode
        or "direct"
    )
    cached = hotspot_res.cached and ppp_res.cached and iface_res.cached

    # Took_ms — wallclock guess. Since sub-fetches were sequential
    # and most likely cached, this is just the slowest one as a
    # reasonable upper bound.
    took_ms = max(hotspot_res.took_ms, ppp_res.took_ms, iface_res.took_ms)

    return mac.MtResult(
        ok=aggregate_ok,
        data=asdict(counters),
        error=combined_error,
        took_ms=took_ms,
        cached=cached,
        dialed_address=dialed,
        mode=mode,
    )


__all__ = ["NasCounters", "counters_for_nas"]
