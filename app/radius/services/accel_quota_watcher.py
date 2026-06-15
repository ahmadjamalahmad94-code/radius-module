"""accel-ppp quota watcher — authoritative 5 GB auto-cutoff.

feat/accel-ppp-radius-attrs (Phase 2a). The ``Session-Octets-Limit`` hint
in accel_attributes is best-effort; THIS module is the authoritative cap.
It accumulates per-session octets from RADIUS interim accounting and, when
a ``vps_accel`` session crosses its quota, sends a **Disconnect-Request**
(RFC 5176) via the existing ``integration.radius_coa.send_disconnect`` —
or, when the plan says ``on_quota_exhaust=reduce_speed``, a CoA to throttle.

Design split (matches vpn_quota.py in the panel):
  * :func:`decide` — a PURE function (no DB, no network) holding ALL the
    policy. Fully unit-testable.
  * :func:`run_once` — thin orchestration. Dependency-injected (sessions
    provider + secret resolver + sender) so it is testable WITHOUT a live
    radacct DB or a live NAS, and ADVISORY-by-default (``enforce=False``):
    it computes + returns the intended actions and sends NOTHING until the
    operator turns enforcement on. Mirrors the proxy's advisory guard.

LAB-PENDING (flagged, not assumed):
  * The live ``sessions`` provider (a FreeRADIUS ``radacct`` query for
    active vps_accel sessions with in/out octets + gigawords) is wired in
    Phase 2c against the real DB — NOT here. ``run_once`` takes it as an
    argument so nothing in this module ever touches production by default.
  * accel-ppp's Disconnect source/secret (loopback vs VPS IP) — confirm in
    lab; passed in via ``nas_secret_for``/session.nas_ip.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# Actions
ACTION_NONE = "none"
ACTION_DISCONNECT = "disconnect"
ACTION_THROTTLE = "throttle"


@dataclass(frozen=True)
class QuotaDecision:
    action: str            # none | disconnect | throttle
    reason: str = ""


def decide(*, used_bytes: int, quota_bytes: int,
           on_exhaust: str = "stop") -> QuotaDecision:
    """Pure policy. ``quota_bytes <= 0`` ⇒ unlimited (never acts).

    on_exhaust:
      * ``stop``         → Disconnect at/over cap (the 5 GB cutoff).
      * ``reduce_speed`` → throttle (caller sends a CoA Filter-Id change).
      * ``notify``       → no enforcement action here (notification is a
                           separate concern); returns none.
    """
    if quota_bytes is None or quota_bytes <= 0:
        return QuotaDecision(ACTION_NONE, "unlimited")
    if used_bytes is None or used_bytes < quota_bytes:
        return QuotaDecision(ACTION_NONE, "under_quota")
    # at or over the cap
    mode = (on_exhaust or "stop").strip().lower()
    if mode == "reduce_speed":
        return QuotaDecision(ACTION_THROTTLE, "quota_exhausted_throttle")
    if mode == "notify":
        return QuotaDecision(ACTION_NONE, "quota_exhausted_notify_only")
    return QuotaDecision(ACTION_DISCONNECT, "quota_exhausted_stop")


@dataclass(frozen=True)
class ActiveSession:
    """One live vps_accel session the watcher evaluates. The Phase-2c live
    provider builds these from radacct; tests build them directly."""
    username: str
    session_id: str
    nas_ip: str
    used_bytes: int
    quota_bytes: int
    on_exhaust: str = "stop"
    framed_ip: str = ""
    calling_station_id: str = ""


@dataclass
class WatcherSummary:
    checked: int = 0
    none: int = 0
    disconnect: int = 0
    throttle: int = 0
    enforced: int = 0          # how many actions were actually SENT
    advisory: bool = True
    actions: list = field(default_factory=list)   # (username, action, reason, sent)


def run_once(
    *,
    sessions: Iterable[ActiveSession],
    nas_secret_for: Callable[[str], str],
    enforce: bool = False,
    sender: Optional[Callable[..., object]] = None,
    coa_throttle: Optional[Callable[..., object]] = None,
) -> WatcherSummary:
    """Evaluate each active session; act only when ``enforce=True``.

    ADVISORY-by-default: with ``enforce=False`` it computes the intended
    action per session and records it, sending NOTHING — safe to run in
    production while the feature is dormant. With ``enforce=True`` it calls
    ``sender`` (default: radius_coa.send_disconnect) for disconnects and
    ``coa_throttle`` for throttles.

    Crash-proof: a single session's send failure is recorded and does not
    abort the pass.
    """
    summary = WatcherSummary(advisory=not enforce)

    if sender is None and enforce:
        # Resolve the real sender lazily so importing this module never
        # pulls the network layer; tests inject their own.
        from ..integration.radius_coa import send_disconnect as _sd
        sender = _sd

    for s in sessions:
        summary.checked += 1
        d = decide(used_bytes=s.used_bytes, quota_bytes=s.quota_bytes,
                   on_exhaust=s.on_exhaust)
        sent = False
        if d.action == ACTION_NONE:
            summary.none += 1
        elif d.action == ACTION_DISCONNECT:
            summary.disconnect += 1
            if enforce and sender is not None:
                try:
                    sender(
                        nas_ip=s.nas_ip,
                        nas_secret=nas_secret_for(s.nas_ip),
                        username=s.username,
                        session_id=s.session_id,
                        framed_ip=s.framed_ip,
                        calling_station_id=s.calling_station_id,
                    )
                    sent = True
                    summary.enforced += 1
                except Exception:  # noqa: BLE001 — never abort the pass
                    sent = False
        elif d.action == ACTION_THROTTLE:
            summary.throttle += 1
            if enforce and coa_throttle is not None:
                try:
                    coa_throttle(s)
                    sent = True
                    summary.enforced += 1
                except Exception:  # noqa: BLE001
                    sent = False
        summary.actions.append((s.username, d.action, d.reason, sent))

    return summary


__all__ = [
    "ACTION_NONE", "ACTION_DISCONNECT", "ACTION_THROTTLE",
    "QuotaDecision", "ActiveSession", "WatcherSummary",
    "decide", "run_once",
]
