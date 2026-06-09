"""device_health_mikrotik — thin MikroTik wrapper for «تتبع حالة الأجهزة».

A focused adapter over the existing admin client (services.mikrotik_admin_client,
aka `mac`). It exposes exactly the reads + the ping the device-health planner
and routes need, plus the WRITE helpers Phase 3 will use — those are gated
behind an explicit live-apply flag so nothing in Phase 1/2 can mutate a router.

Reads (safe, Phase 2):
  • read_ip_addresses(nas)   → /ip/address/print
  • read_ip_bindings(nas)    → /ip/hotspot/ip-binding/print
  • read_netwatch(nas)       → /tool/netwatch/print
  • read_router_state(nas)   → {ok, addresses, bindings, netwatch, errors}
  • ping(nas, target, count) → /ping (diagnostic)

Writes (Phase 3 — NOT wired to any route here):
  • add_ip_address / add_ip_binding / add_netwatch
  Each refuses unless `live=True` AND env HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY is
  truthy, so an accidental call still cannot touch a live router.

The managed-comment marker lets a future remove step sweep only our rows.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from . import mikrotik_admin_client as mac

MANAGED_COMMENT = "managed-by device-health"


def managed_comment(device_id: Optional[int] = None) -> str:
    if device_id:
        return f"{MANAGED_COMMENT} device_id={int(device_id)}"
    return MANAGED_COMMENT


# ── reads ──────────────────────────────────────────────────────

def read_ip_addresses(nas: Mapping[str, Any]) -> mac.MtResult:
    """/ip/address/print — address ↔ interface map. Reuses the admin
    client's cached fetcher (TTL_SYSTEM)."""
    return mac.ip_addresses(nas)


def read_ip_bindings(nas: Mapping[str, Any]) -> mac.MtResult:
    """/ip/hotspot/ip-binding/print — every hotspot IP binding."""
    return mac.fetch_cached(
        nas=nas,
        operation="hotspot/ip-binding",
        ttl_sec=mac.TTL_SYSTEM,
        work=lambda c: list(c.print_("/ip/hotspot/ip-binding/print")),
    )


def read_netwatch(nas: Mapping[str, Any]) -> mac.MtResult:
    """/tool/netwatch/print — every netwatch host."""
    return mac.fetch_cached(
        nas=nas,
        operation="tool/netwatch",
        ttl_sec=mac.TTL_HEALTH,
        work=lambda c: list(c.print_("/tool/netwatch/print")),
    )


def read_router_state(nas: Mapping[str, Any]) -> dict:
    """Fetch all three lists the planner diffs against. Soft-fails per
    resource: a failed read yields an empty list + an error string so the
    planner still produces a (conservative) plan and the UI can surface
    which read failed."""
    out: dict = {"ok": True, "addresses": [], "bindings": [],
                 "netwatch": [], "errors": {}}
    addr = read_ip_addresses(nas)
    if addr.ok:
        out["addresses"] = addr.data or []
    else:
        out["ok"] = False
        out["errors"]["addresses"] = addr.error
    bind = read_ip_bindings(nas)
    if bind.ok:
        out["bindings"] = bind.data or []
    else:
        out["ok"] = False
        out["errors"]["bindings"] = bind.error
    nw = read_netwatch(nas)
    if nw.ok:
        out["netwatch"] = nw.data or []
    else:
        out["ok"] = False
        out["errors"]["netwatch"] = nw.error
    return out


def ping(nas: Mapping[str, Any], *, target: str, count: int = 4) -> mac.MtResult:
    """Diagnostic ping from the router to the device. Read-only."""
    return mac.tool_ping(nas, target=target, count=count)


# ── writes (Phase 3 — gated, NOT wired to any route in this delivery) ──

def live_apply_enabled() -> bool:
    """Master deployment gate for live MikroTik writes. Default OFF so the
    feature ships safe; the operator sets the env var to allow apply."""
    flag = (os.environ.get("HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _live_apply_allowed(live: bool) -> tuple[bool, str]:
    if not live:
        return False, "التطبيق الحيّ غير مُفعّل لهذا الطلب (dry-run)."
    if not live_apply_enabled():
        return False, ("التطبيق الحيّ على الراوتر معطّل — "
                       "اضبط HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY لتفعيله (Phase 3).")
    return True, ""


def _created_id(result: mac.MtResult) -> str:
    """Best-effort extraction of the new row's .id from an add reply.
    RouterOS returns it as `ret` on the !done reply; tolerate any shape."""
    if not result.ok:
        return ""
    rows = result.data if isinstance(result.data, list) else []
    for s in rows:
        if not isinstance(s, dict):
            continue
        attrs = s.get("attrs") or {}
        rid = attrs.get("ret") or attrs.get(".id") or s.get("ret")
        if rid:
            return str(rid)
    return ""


def add_ip_address(
    nas: Mapping[str, Any], *, address: str, interface: str,
    device_id: Optional[int] = None, live: bool = False,
) -> mac.MtResult:
    ok, why = _live_apply_allowed(live)
    if not ok:
        return mac.MtResult(ok=False, error=why)
    attrs = {"address": str(address), "interface": str(interface),
             "comment": managed_comment(device_id)}
    res = mac._run_mutation(
        nas, operation="ip/address/add",
        work=lambda c: c.run("/ip/address/add", attrs=attrs),
        invalidate=("ip/addresses",),
    )
    return _with_created_id(res)


def add_ip_binding(
    nas: Mapping[str, Any], *, address: str, binding_type: str = "bypassed",
    device_id: Optional[int] = None, live: bool = False,
) -> mac.MtResult:
    ok, why = _live_apply_allowed(live)
    if not ok:
        return mac.MtResult(ok=False, error=why)
    attrs = {"address": str(address), "type": str(binding_type),
             "comment": managed_comment(device_id)}
    res = mac._run_mutation(
        nas, operation="hotspot/ip-binding/add",
        work=lambda c: c.run("/ip/hotspot/ip-binding/add", attrs=attrs),
        invalidate=("hotspot/ip-binding",),
    )
    return _with_created_id(res)


def add_netwatch(
    nas: Mapping[str, Any], *, host: str, interval_sec: int = 60,
    timeout_sec: int = 3, device_id: Optional[int] = None, live: bool = False,
) -> mac.MtResult:
    ok, why = _live_apply_allowed(live)
    if not ok:
        return mac.MtResult(ok=False, error=why)
    attrs = {"host": str(host), "type": "simple",
             "interval": _hms(interval_sec), "timeout": _hms(timeout_sec),
             "comment": managed_comment(device_id)}
    res = mac._run_mutation(
        nas, operation="tool/netwatch/add",
        work=lambda c: c.run("/tool/netwatch/add", attrs=attrs),
        invalidate=("tool/netwatch",),
    )
    return _with_created_id(res)


def _with_created_id(res: mac.MtResult) -> mac.MtResult:
    """Return a copy of `res` whose .data is the created row id (string).
    Keeps ok/error/took_ms so callers see one consistent envelope."""
    if not res.ok:
        return res
    return mac.MtResult(ok=True, data=_created_id(res), error="",
                        took_ms=res.took_ms, dialed_address=res.dialed_address,
                        mode=res.mode)


def _hms(seconds: int) -> str:
    """RouterOS time format HH:MM:SS (>= 1s)."""
    s = max(1, int(seconds))
    h, rem = divmod(s, 3600)
    m, s2 = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s2:02d}"
