"""Live session control via RADIUS CoA / Disconnect-Message (RFC 5176).

Three owner-triggered, per-session actions on a LIVE radacct row:

  (a) change_ip_live  — pushes a new Framed-IP-Address to the running
      session (the «تغيير IP المواقع» paid service, item #17). Owner
      proved this live: on a PPPoE active session, sending a CoA-Request
      with Framed-IP-Address swaps the connected user's IP WITHOUT
      disconnect.
  (b) change_speed_live — pushes a new Mikrotik-Rate-Limit so a speed
      change applies to the running session instantly (no reconnect).
      Per-direction in "<rxK> <txK>" format that MikroTik accepts.
  (c) disconnect_live — Disconnect-Message to drop the live session.
      Already covered by the existing CoA layer; surfaced here too so
      every live action has a single entry-point with consistent
      session-type semantics.

Session-type support (verified vs MikroTik docs + owner's live test):

                          PPPoE          HOTSPOT
    set IP                 ✓ (proven)    ✗ (NAK — see below)
    set speed              ✓             ✓ (per-session if framed_ip
                                            + Calling-Station-Id are sent)
    disconnect             ✓             ✓

MikroTik's hotspot active table is keyed by username+IP+MAC; a CoA that
changes Framed-IP-Address is not honored — the platform either replies
NAK ("can't change client ip on hotspot") or silently re-resolves at
next renewal. We surface this as an explicit `unsupported` outcome
rather than sending a packet we know will fail.

NO background mutation. Every call here is the direct effect of an
owner button. No retry-loop; one packet, one result. NAK / timeout are
surfaced verbatim — there is never a fake-success path.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address
from typing import Optional

from ..integration.radius_coa import (
    CoaResult,
    find_all_nas_for_sessions,
    send_coa,
    send_disconnect,
)


_LOG = logging.getLogger(__name__)


# ── session-type classification ──────────────────────────────────────


# MikroTik writes the NAS-Port-Type as either "PPPoE" / "PPP" for
# broadband sessions or "Ethernet" / "HotSpot" / "Wireless-Other" for
# hotspot. Callers can also pass the raw radacct/online row and we
# best-effort detect.
_PPPOE_TYPES   = {"ppp", "pppoe", "virtual", "ppp_over_vpn"}
_HOTSPOT_TYPES = {"ethernet", "hotspot", "wireless-other", "wireless"}


def _normalize_session_type(raw: str) -> str:
    s = (raw or "").strip().lower().replace("_", " ").replace("-", " ")
    # MikroTik writes the NAS-Port-Type as "Virtual" for PPPoE sessions
    # (that's the literal value the radacct accounting carries). The IETF
    # nasporttype "Virtual" maps to a tunnelled PPP session — for our
    # purposes that's broadband.
    if any(tok in s for tok in ("pppoe", "ppp", "virtual")):
        return "pppoe"
    if any(tok in s for tok in ("hotspot", "ethernet", "wireless")):
        return "hotspot"
    return ""


def detect_session_type(row: dict) -> str:
    """Return 'pppoe' / 'hotspot' / '' (unknown) for one session row.

    Accepts the dict shape used by `find_all_nas_for_sessions` augmented
    with the radacct columns `nasporttype`, `servicetype`, and (cheap
    fallback) the `acctsessionid` prefix MikroTik uses for hotspot
    (typically starts with `8`/`a`/`b` — but we don't rely on that).
    """
    for key in ("nasporttype", "nas_port_type", "service_type", "servicetype"):
        v = _normalize_session_type(str(row.get(key) or ""))
        if v:
            return v
    return ""


# ── rate-limit helpers (Mikrotik format) ─────────────────────────────


def build_mikrotik_rate_limit(rx_kbps: int, tx_kbps: int) -> str:
    """Encode a (rx, tx) pair into the MikroTik vendor format MT accepts.

    rx = download (NAS→client), tx = upload (client→NAS). MT honors the
    short form "<rxK> <txK>" inside Mikrotik-Rate-Limit on CoA.

    Returns the encoded string; raises ValueError on non-positive input
    so callers can surface "invalid speed" without sending a packet.
    """
    rx = int(rx_kbps)
    tx = int(tx_kbps)
    if rx <= 0 or tx <= 0:
        raise ValueError("rx and tx must both be positive (kbps)")
    if rx > 10_000_000 or tx > 10_000_000:  # 10 Gbps sanity
        raise ValueError("rate exceeds 10G — refusing to encode")
    return f"{rx}k {tx}k"


# ── input validation ─────────────────────────────────────────────────


def _validate_ipv4(raw: str) -> IPv4Address:
    """Reject anything that isn't an unambiguous IPv4 literal so we
    NEVER send a malformed Framed-IP-Address to a MikroTik in production.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("ip address required")
    try:
        addr = ip_address(s)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid ipv4 literal: {s!r}") from exc
    if not isinstance(addr, IPv4Address):
        raise ValueError("only IPv4 supported for Framed-IP-Address CoA")
    if addr.is_loopback or addr.is_multicast or addr.is_reserved:
        raise ValueError(f"{s} is a loopback/multicast/reserved address")
    return addr


# ── result envelope (extends CoaResult with extra hints) ─────────────


@dataclass(frozen=True)
class LiveControlOutcome:
    """One per (session, action) outcome.

    Designed to be JSON-serialised straight into a route response or a
    flash message. `code` mirrors the RFC 5176 reply code so the UI can
    differentiate ACK (44/41), NAK (45/42), timeout, and unsupported.
    """
    ok: bool
    code: int
    code_name: str
    reply_message: str
    session_type: str
    session_id: str
    nas_ip: str
    action: str       # "set_ip" / "set_speed" / "disconnect"
    detail: str = ""  # short human hint; never contains the shared secret

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "code": self.code, "code_name": self.code_name,
            "reply_message": self.reply_message,
            "session_type": self.session_type, "session_id": self.session_id,
            "nas_ip": self.nas_ip, "action": self.action, "detail": self.detail,
        }


def _unsupported(action: str, session_type: str, *, session_id: str,
                 nas_ip: str, detail: str) -> LiveControlOutcome:
    return LiveControlOutcome(
        ok=False, code=0, code_name="unsupported",
        reply_message=detail,
        session_type=session_type, session_id=session_id, nas_ip=nas_ip,
        action=action, detail=detail,
    )


def _wrap(coa: CoaResult, *, action: str, session_type: str,
          session_id: str, nas_ip: str, detail: str = "") -> LiveControlOutcome:
    return LiveControlOutcome(
        ok=bool(coa.ok), code=int(coa.code or 0),
        code_name=str(coa.code_name or ""),
        reply_message=str(coa.reply_message or ""),
        session_type=session_type, session_id=session_id, nas_ip=nas_ip,
        action=action, detail=detail,
    )


def _no_session(action: str) -> LiveControlOutcome:
    return LiveControlOutcome(
        ok=False, code=0, code_name="no_active_session",
        reply_message="لا توجد جلسة نشطة مطابقة",
        session_type="", session_id="", nas_ip="",
        action=action, detail="no active radacct row matched",
    )


# ── public API: three actions ────────────────────────────────────────


def change_ip_live(*, tenant_id: int, username: str, new_ip: str,
                   session_id: str = "",
                   session_row: Optional[dict] = None) -> LiveControlOutcome:
    """Action (a) — push a new Framed-IP-Address to the live session.

    Hotspot sessions DON'T accept this via CoA (MT rejects with a NAK
    that's basically "use /ip hotspot active set-ip"). We surface that
    as `unsupported` without sending a packet. PPPoE sessions accept it
    and the user does NOT disconnect (owner's live proof).
    """
    addr = _validate_ipv4(new_ip)  # raises before any side-effect
    session = _pick_session(tenant_id, username, session_id, session_row)
    if session is None:
        return _no_session("set_ip")
    stype = detect_session_type(session)
    if stype == "hotspot":
        return _unsupported(
            "set_ip", "hotspot",
            session_id=session.get("session_id", ""),
            nas_ip=session.get("nas_ip", ""),
            detail="MikroTik hotspot doesn't accept Framed-IP-Address via CoA; "
                   "the live ip set must come via /ip hotspot active set-ip — "
                   "use the router action instead, not RADIUS CoA.",
        )
    # default to pppoe semantics (verified live) for unknown types too;
    # the NAS will NAK if it really doesn't support it.
    coa = send_coa(
        nas_ip=session["nas_ip"], nas_secret=session["nas_secret"],
        username=username,
        session_id=session.get("session_id", ""),
        framed_ip=session.get("framed_ip", ""),     # match key
        calling_station_id=session.get("calling_station_id", ""),
        new_framed_ip=str(addr),                    # the change
        port=session.get("coa_port", 3799),
    )
    return _wrap(
        coa, action="set_ip", session_type=stype or "pppoe",
        session_id=session.get("session_id", ""),
        nas_ip=session.get("nas_ip", ""),
        detail=f"new_framed_ip={addr}",
    )


def change_speed_live(*, tenant_id: int, username: str,
                      rx_kbps: int, tx_kbps: int,
                      session_id: str = "",
                      session_row: Optional[dict] = None) -> LiveControlOutcome:
    """Action (b) — push a new Mikrotik-Rate-Limit to the live session.

    Works for both PPPoE and hotspot (when framed_ip + Calling-Station-Id
    are passed for the match — which we always do).
    """
    rate = build_mikrotik_rate_limit(rx_kbps, tx_kbps)  # raises on bad input
    session = _pick_session(tenant_id, username, session_id, session_row)
    if session is None:
        return _no_session("set_speed")
    stype = detect_session_type(session)
    coa = send_coa(
        nas_ip=session["nas_ip"], nas_secret=session["nas_secret"],
        username=username,
        session_id=session.get("session_id", ""),
        framed_ip=session.get("framed_ip", ""),
        calling_station_id=session.get("calling_station_id", ""),
        new_rate_limit=rate,
        port=session.get("coa_port", 3799),
    )
    return _wrap(
        coa, action="set_speed", session_type=stype or "unknown",
        session_id=session.get("session_id", ""),
        nas_ip=session.get("nas_ip", ""),
        detail=f"rate={rate}",
    )


def disconnect_live(*, tenant_id: int, username: str,
                    session_id: str = "",
                    session_row: Optional[dict] = None) -> LiveControlOutcome:
    """Action (c) — Disconnect-Message to drop the live session.

    Works for both PPPoE and hotspot.
    """
    session = _pick_session(tenant_id, username, session_id, session_row)
    if session is None:
        return _no_session("disconnect")
    stype = detect_session_type(session)
    coa = send_disconnect(
        nas_ip=session["nas_ip"], nas_secret=session["nas_secret"],
        username=username,
        session_id=session.get("session_id", ""),
        framed_ip=session.get("framed_ip", ""),
        calling_station_id=session.get("calling_station_id", ""),
        port=session.get("coa_port", 3799),
    )
    return _wrap(
        coa, action="disconnect", session_type=stype or "unknown",
        session_id=session.get("session_id", ""),
        nas_ip=session.get("nas_ip", ""),
    )


# ── session selection ────────────────────────────────────────────────


def _pick_session(tenant_id: int, username: str,
                  session_id: str, override: Optional[dict]) -> Optional[dict]:
    """Resolve the radacct row + NAS info needed to build a CoA packet.

    When `override` is supplied (tests, single-session UIs) it's used
    verbatim. Otherwise we look up live sessions for `username` and pick
    either the one matching `session_id` or — if no id given — the most
    recent one. A missing/disabled NAS row → return None so callers
    surface a clean "no_active_session" (rather than send unsigned).
    """
    if override is not None:
        return override
    sessions = find_all_nas_for_sessions(tenant_id, username)
    if not sessions:
        return None
    if session_id:
        wanted = session_id.strip()
        for s in sessions:
            if s.get("session_id") == wanted:
                return s
        return None
    return sessions[0]


# ── support matrix (for UI + docs) ───────────────────────────────────


SUPPORT_MATRIX = {
    "pppoe": {
        "set_ip":     True,
        "set_speed":  True,
        "disconnect": True,
    },
    "hotspot": {
        "set_ip":     False,   # MikroTik doesn't honour it via CoA
        "set_speed":  True,
        "disconnect": True,
    },
}


def is_supported(action: str, session_type: str) -> bool:
    return bool(SUPPORT_MATRIX.get(session_type, {}).get(action))


__all__ = [
    "LiveControlOutcome",
    "build_mikrotik_rate_limit",
    "change_ip_live",
    "change_speed_live",
    "detect_session_type",
    "disconnect_live",
    "is_supported",
    "SUPPORT_MATRIX",
]
