"""K1.3 — VPN reachability probe for WireGuard peers.

Two checks the rest of the admin reads:

1. `latest_handshake_age(public_key)` — parses
   `wg show <iface> latest-handshakes` (or `dump`) and returns the
   seconds since this peer's last handshake. `None` when the peer
   has never been seen.

2. `is_peer_alive(peer_ip)` — fires a single ICMP ping with a tight
   timeout and a process-wide TTL cache so the dashboard auto-
   refresh doesn't ping every peer once per second.

Both functions are intentionally defensive:
- They never raise — a missing `wg`/`ping` binary returns
  `(None | False)` so the admin can render "VPN status unknown"
  rather than 500-ing.
- They time-bound subprocess calls.
- They cache results aggressively (default 30 s) so a 60-second
  dashboard refresh consults at most one fresh probe per peer.

Used by:
- The probe worker (K1.4 follow-up) that updates
  `nas_devices.vpn_last_handshake_ts` every 60 s.
- The dashboard status chip (K9) that decides 🟢/🟡/🔴.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

_LOG = logging.getLogger(__name__)

# ── Status thresholds (seconds) ──────────────────────────────────
# Mirrors WireGuard's own keepalive defaults (25 s) plus enough
# slack for transient packet loss.
FRESH_HANDSHAKE_MAX = 180   # 🟢 ≤ 3 min since last handshake
SLOW_HANDSHAKE_MAX = 600    # 🟡 3–10 min — peer probably alive
                            # 🔴 > 10 min  — peer disconnected

# Cache TTL for ping probes (seconds).
_PING_CACHE_TTL = 30.0

# Cache TTL for `wg show … dump` parsing (seconds).
_WG_DUMP_CACHE_TTL = 10.0


@dataclass(frozen=True)
class VpnStatus:
    """Status verdict for one VPN peer.

    `bucket` ∈ {'fresh', 'slow', 'stale', 'unknown'} so the UI
    can paint the chip the right colour without re-deriving the
    threshold.
    """
    bucket: str
    handshake_age_sec: Optional[int]
    peer_alive: Optional[bool]
    note: str = ""

    @property
    def is_fresh(self) -> bool:
        return self.bucket == "fresh"

    @property
    def is_stale(self) -> bool:
        return self.bucket == "stale"


_ping_cache: dict[str, tuple[float, bool]] = {}
_wg_dump_cache: tuple[float, dict[str, int]] | None = None


def _now() -> float:
    return time.time()


def is_peer_alive(peer_ip: str, *, timeout_sec: float = 1.5) -> Optional[bool]:
    """Single-ping ICMP probe.

    Returns:
        True   — ping responded within timeout
        False  — no response (peer probably offline / blocking ICMP)
        None   — `ping` binary missing / not allowed (treat as unknown)

    Cached per peer for `_PING_CACHE_TTL` so a 1 s UI refresh
    doesn't pin the network.
    """
    if not peer_ip:
        return None
    now = _now()
    cached = _ping_cache.get(peer_ip)
    if cached and now - cached[0] <= _PING_CACHE_TTL:
        return cached[1]

    ping_bin = shutil.which("ping")
    if not ping_bin:
        return None  # unknown environment — let the UI show "?"

    # `-c 1` Linux, `-n 1` Windows. We're targeting the Linux VPS
    # but defend either way.
    is_windows = subprocess.run(
        ["uname"], capture_output=True, text=True,
    ).returncode != 0
    args = (
        [ping_bin, "-n", "1", "-w", str(int(timeout_sec * 1000)), peer_ip]
        if is_windows else
        [ping_bin, "-c", "1", "-W", str(int(max(timeout_sec, 1))), peer_ip]
    )
    try:
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=timeout_sec + 2,
        )
        alive = result.returncode == 0
    except subprocess.TimeoutExpired:
        alive = False
    except Exception:  # pragma: no cover — defensive
        return None
    _ping_cache[peer_ip] = (now, alive)
    return alive


def latest_handshake_age(
    public_key: str, *, interface: str = "wg0",
) -> Optional[int]:
    """Return seconds since the most recent handshake for this peer.

    Reads `wg show <iface> dump` (one syscall, all peers) and caches
    the parsed map for `_WG_DUMP_CACHE_TTL` seconds so multiple
    calls during a single dashboard render share the same dump.

    Returns:
        int  — age in seconds, fresh value
        0    — handshake just happened
        None — peer not found in dump / `wg` binary missing / no
               handshake yet (the `latest-handshake` column is `0`)
    """
    if not public_key:
        return None
    dump = _read_wg_dump(interface)
    if not dump:
        return None
    last_ts = dump.get(public_key.strip())
    if last_ts is None or last_ts == 0:
        return None
    age = max(int(_now() - last_ts), 0)
    return age


def _read_wg_dump(interface: str) -> dict[str, int]:
    """Parse `wg show <iface> dump` into {pub_key: last_handshake_epoch}.

    The dump format is tab-separated; the first row is the
    interface itself, subsequent rows are peers. Peer columns:
        pub_key  preshared_key  endpoint  allowed_ips
        latest_handshake  rx_bytes  tx_bytes  persistent_keepalive

    Returns an empty dict if `wg` is missing or fails — the caller
    falls back to "unknown" rather than failing.
    """
    global _wg_dump_cache
    now = _now()
    if _wg_dump_cache and now - _wg_dump_cache[0] <= _WG_DUMP_CACHE_TTL:
        return _wg_dump_cache[1]

    wg_bin = shutil.which("wg")
    if not wg_bin:
        return {}
    try:
        result = subprocess.run(
            [wg_bin, "show", interface, "dump"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return {}
    except Exception:  # pragma: no cover — defensive
        return {}

    parsed: dict[str, int] = {}
    lines = result.stdout.splitlines()
    # First line is the interface; peers follow.
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        pub_key, _psk, _endpoint, _allowed, latest = cols[:5]
        try:
            parsed[pub_key.strip()] = int(latest)
        except (TypeError, ValueError):
            continue
    _wg_dump_cache = (now, parsed)
    return parsed


def status_for(
    *,
    public_key: str = "",
    peer_ip: str = "",
    interface: str = "wg0",
) -> VpnStatus:
    """Combined verdict for a NAS row.

    Prefers the handshake-age signal (cheaper, always accurate
    when `wg` is available). Falls back to the ICMP ping when the
    handshake check is inconclusive.

    The bucket maps onto UI colours:
        fresh   → 🟢 'VPN ✓ متصل'
        slow    → 🟡 'VPN ⚠ بطيء'
        stale   → 🔴 'VPN ✗ مفصول'
        unknown → ⚪ 'VPN — ?'
    """
    age = latest_handshake_age(public_key, interface=interface)
    if age is not None:
        if age <= FRESH_HANDSHAKE_MAX:
            return VpnStatus("fresh", age, True, "")
        if age <= SLOW_HANDSHAKE_MAX:
            return VpnStatus("slow", age, None, "آخر handshake قديم")
        return VpnStatus("stale", age, False, "لا توجد handshake حديثة")

    # No handshake yet (or wg unavailable) → fall back to ping
    alive = is_peer_alive(peer_ip)
    if alive is True:
        return VpnStatus("slow", None, True, "ping ناجح، لا handshake")
    if alive is False:
        return VpnStatus("stale", None, False, "ping فاشل")
    return VpnStatus("unknown", None, None, "wg / ping غير متاح")


def clear_caches() -> None:
    """For tests: empty the ping + wg-dump caches."""
    global _wg_dump_cache
    _ping_cache.clear()
    _wg_dump_cache = None
