"""device_health_poller — Phase 4 polling + status for «تتبع حالة الأجهزة».

One sweep (`tick`) determines each monitored device's health from the router's
Netwatch state, falling back to a MikroTik ping probe for latency / when no
Netwatch row exists. It updates the device status (up/down/timeout/
high_latency/unknown), writes a status-change event, and — from Phase 5 —
dispatches deduplicated alerts.

Design notes:
  • Read-only against the router (Netwatch print + ping). No mutation.
  • Grouped by router so Netwatch is read once per router per sweep.
  • Safe to run with NO live device: every router read is wrapped by the admin
    client's error envelope; an unreachable router yields status 'unknown'.
  • NOT auto-started — call tick() on demand (the «فحص الكل» button / a future
    scheduler hook). start()/stop() provide an opt-in background loop gated by
    HOBERADIUS_DEVICE_HEALTH_POLL so a disconnected router is never hammered.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

from ..db.repos import device_health_repo as repo
from ..db.repos import nas_repo

_LOG = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 60.0


# ── one sweep ──────────────────────────────────────────────────

def tick(tenant_id: Optional[int] = None,
         *, mt=None, alert_fn: Optional[Callable] = None) -> dict:
    """Poll every monitoring-enabled device (optionally one tenant).

    `mt` / `alert_fn` are injectable for tests; production uses the real
    device_health_mikrotik wrapper and the Phase-5 alert dispatcher.
    Returns a summary dict.
    """
    if mt is None:
        from . import device_health_mikrotik as mt  # noqa: PLW0127
    if alert_fn is None:
        alert_fn = _default_alert_fn

    devices = _watched_devices(tenant_id)
    summary = {"scanned": len(devices), "up": 0, "down": 0,
               "high_latency": 0, "unknown": 0, "changed": 0, "alerts": 0}

    # Group by (tenant_id, router_id) so we read Netwatch once per router.
    by_router: dict[tuple[int, int], list[dict]] = {}
    for d in devices:
        by_router.setdefault((d["tenant_id"], d["router_id"]), []).append(d)

    for (tid, router_id), group in by_router.items():
        nas = nas_repo.get_nas(tid, router_id)
        if not nas:
            for d in group:
                _commit_status(tid, d, "unknown", None, summary, alert_fn)
            continue
        nas_dict = _nas_to_dict(nas)
        nw = mt.read_netwatch(nas_dict)
        nw_rows = nw.data if nw.ok else []
        nw_ok = nw.ok
        for d in group:
            status, latency = _derive_status(d, nw_rows, nw_ok, nas_dict, mt)
            _commit_status(tid, d, status, latency, summary, alert_fn)

    _LOG.info("[device-health] tick scanned=%d up=%d down=%d high=%d "
              "unknown=%d changed=%d alerts=%d",
              summary["scanned"], summary["up"], summary["down"],
              summary["high_latency"], summary["unknown"], summary["changed"],
              summary["alerts"])
    return summary


def _derive_status(device, nw_rows, nw_ok, nas_dict, mt) -> tuple[str, Optional[float]]:
    """Resolve one device's status via the SHARED reachability probe so Sync-All
    NEVER contradicts the manual «فحص بنج». A direct ping is the source of truth;
    applied netwatch only resolves a ping-down / can't-ping case; missing netwatch
    data yields «unknown», never a false «مفصول».

    The poller already read netwatch once for this router, so we hand those rows
    in (when the read succeeded) and tell the probe not to re-read."""
    ip = (device.get("ip_address") or "").strip()
    if not ip:
        return "unknown", None
    from . import device_health as svc
    probe = svc.probe_reachability(
        device, nas_dict, mt=mt,
        netwatch_rows=(nw_rows if nw_ok else None),
        read_netwatch=False)
    return probe["status"], probe["latency_ms"]


def _commit_status(tid, device, status, latency, summary, alert_fn) -> None:
    prev = device.get("status") or "unknown"
    repo.set_status(tenant_id=tid, device_id=device["id"],
                    status=status, latency_ms=latency)
    if status == "up":
        summary["up"] += 1
    elif status in ("down", "timeout"):
        summary["down"] += 1
    elif status == "high_latency":
        summary["high_latency"] += 1
    elif status == "unknown":
        summary["unknown"] += 1

    if status != prev:
        summary["changed"] += 1
        repo.add_event(tenant_id=tid, device_id=device["id"],
                       event_type=status, previous_status=prev,
                       new_status=status, latency_ms=latency,
                       message=_event_message(device, prev, status, latency))

    # Phase 5 alert dispatch — fed the FRESH device row (counters updated by
    # set_status above) so cooldown/threshold logic sees the current state.
    fresh = repo.get_device(tid, device["id"]) or device
    fired = alert_fn(tenant_id=tid, device=fresh, prev_status=prev,
                     new_status=status, latency_ms=latency)
    if fired:
        summary["alerts"] += len(fired) if isinstance(fired, (list, tuple)) else 1


def _event_message(device, prev, status, latency) -> str:
    name = device.get("name") or f"#{device.get('id')}"
    if status in ("down", "timeout"):
        return f"انقطع الاتصال مع «{name}»."
    if status == "up" and prev in ("down", "timeout", "unknown"):
        lat = f" — البنج {latency} ms" if latency is not None else ""
        return f"عاد الاتصال مع «{name}»{lat}."
    if status == "high_latency":
        return f"ارتفاع البنج على «{name}» ({latency} ms)."
    return f"تغيّرت حالة «{name}» إلى {status}."


def _default_alert_fn(**kwargs):
    """Phase 4 default = no alerts (Phase 5 replaces this with the dispatcher).
    Imported lazily so Phase 4 carries no dependency on the alerts module."""
    try:
        from . import device_health_alerts
    except ImportError:
        return []
    return device_health_alerts.evaluate_and_dispatch(**kwargs)


# ── helpers ────────────────────────────────────────────────────

def _watched_devices(tenant_id: Optional[int]) -> list[dict]:
    if tenant_id is not None:
        return repo.list_devices(int(tenant_id), monitoring_only=True)
    # Sweep every tenant that owns a monitored device.
    from ..db.connection import db
    cur = db().execute(
        "SELECT DISTINCT tenant_id FROM network_device_monitor_devices "
        "WHERE monitoring_enabled = 1 AND deleted_at IS NULL")
    out: list[dict] = []
    for r in cur.fetchall():
        out.extend(repo.list_devices(int(r["tenant_id"]), monitoring_only=True))
    return out


def _nas_to_dict(nas) -> dict:
    return {
        "id": nas.id, "tenant_id": nas.tenant_id, "name": nas.name,
        "address": nas.address, "api_port": nas.api_port,
        "api_user": nas.api_user, "api_password": nas.api_password,
        "api_use_tls": nas.api_use_tls,
        "api_timeout_sec": getattr(nas, "api_timeout_sec", 3) or 3,
    }


# ── opt-in background loop (NOT auto-started) ──────────────────

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_lock = threading.Lock()


def poll_enabled() -> bool:
    return (os.environ.get("HOBERADIUS_DEVICE_HEALTH_POLL") or "").strip().lower() \
        in ("1", "true", "yes", "on")


def start(app=None) -> bool:
    """Start the singleton sweep loop — ONLY if HOBERADIUS_DEVICE_HEALTH_POLL is
    set. Returns True if started. Not called from app startup by default."""
    if not poll_enabled():
        return False
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="device-health-poller",
                                   daemon=True)
        _thread.start()
    _LOG.info("[device-health] poller started — every %.0fs", POLL_INTERVAL_SEC)
    return True


def stop() -> None:
    _stop.set()


def _loop() -> None:
    while not _stop.wait(POLL_INTERVAL_SEC):
        try:
            tick()
        except Exception:  # noqa: BLE001
            _LOG.exception("[device-health] tick failed — continuing")
