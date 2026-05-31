"""Network Device Monitor — periodic ping cron (Sprint 2).

One singleton background thread that:

  1. Wakes every `POLL_INTERVAL_SEC` (default 60 s).
  2. Loads every device with `watch_enabled = 1` across every
     tenant.
  3. Probes each via TCP-connect to its management_port.
  4. Writes a row to `network_device_checks` + updates the
     parent `network_devices` row's last_status/latency_ms.
  5. Detects state flips (up↔down) and fires Telegram alerts
     for devices that ALSO have `alert_enabled = 1`, with
     per-(device, event) cooldown to prevent spam.

Single-process model — relies on Flask running with one
worker. If you scale to gunicorn `-w N`, put a process-id /
file-lock check at the top of `_loop()` so only one worker
runs the cron. (Out of scope for sprint 2; flagged in
NETWORK_OPERATIONS_PLAN.md «Out of scope».)

Idempotency: the worker NEVER calls itself. If a tick is
still running when the next interval expires, the second
wake-up sleeps another interval. (`threading.Event.wait()`
serialises us.)
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from ..db.repos import (
    network_device_checks_repo,
    network_devices_repo,
    tenant_telegram_settings_repo,
)
from . import telegram_notifier

_LOG = logging.getLogger(__name__)

# Tunables — tweakable without re-reading the file.
POLL_INTERVAL_SEC = 60.0       # how often we sweep watched devices
TCP_TIMEOUT_SEC   = 2.0        # per-device probe ceiling
HIGH_LATENCY_MS   = 150.0      # threshold for «device_high_latency» alert
# Cooldown per event-type — don't re-fire the same event for
# the same device within this window. device_down fires once,
# device_up fires once; high_latency repeats at most every 15
# minutes so the operator isn't spammed for chronic problems.
COOLDOWN_SEC: dict[str, int] = {
    "device_down":         5 * 60,        # 5 min repeat-suppress
    "device_up":           5 * 60,
    "device_high_latency": 15 * 60,       # 15 min
}


# ─── public API ────────────────────────────────────────────────


_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_lock = threading.Lock()


def start(app=None) -> None:
    """Spawn the singleton worker. Safe to call multiple times —
    subsequent calls are no-ops while the previous thread is
    alive. The `app` argument is accepted for symmetry with
    Flask extension patterns but isn't currently needed
    (everything reads through repos that pick up the request
    context's tenant_id; the cron runs outside Flask's
    request lifecycle and queries by explicit tenant_id).
    """
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_loop,
            name="net-device-monitor",
            daemon=True,
        )
        _thread.start()
    _LOG.info("[net-monitor] started — poll every %.0fs",
              POLL_INTERVAL_SEC)


def stop() -> None:
    """Tell the worker to exit. Useful for unit tests; the
    daemon thread dies with the process otherwise."""
    _stop.set()


# ─── worker loop ───────────────────────────────────────────────


def _loop() -> None:
    """Sleeps `POLL_INTERVAL_SEC` between ticks. `_stop.wait`
    returns True if asked to exit, False on timeout — so we
    use the timeout case as the «do work» signal."""
    while not _stop.wait(POLL_INTERVAL_SEC):
        try:
            tick()
        except Exception:  # noqa: BLE001
            _LOG.exception("[net-monitor] tick failed — continuing")


def tick() -> dict:
    """One sweep. Returns a small summary dict for tests.
    Public so a route can call it on-demand (e.g. «اعد فحص
    الكل الآن» button) without going through the cron schedule.
    """
    started = time.perf_counter()
    devices = _all_watched()
    summary = {
        "scanned":  len(devices),
        "up":       0,
        "down":     0,
        "alerts":   0,
    }
    for device in devices:
        try:
            new_status, latency_ms, err = _probe(device)
        except Exception:  # noqa: BLE001
            _LOG.exception("[net-monitor] probe failed device=%s",
                           device.get("id"))
            continue
        prev_status = device.get("last_status") or "unknown"

        # Persist history + parent row.
        network_device_checks_repo.record_check(
            device_id=device["id"],
            status=new_status,
            latency_ms=latency_ms,
            error_message=err,
            source="backend_ping",
        )
        network_devices_repo.set_last_check(
            tenant_id=device["tenant_id"],
            device_id=device["id"],
            status=new_status,
            latency_ms=latency_ms,
        )

        # Aggregate counts.
        if new_status == "up":   summary["up"]   += 1
        if new_status == "down": summary["down"] += 1

        # Alert dispatch — only on state flip (no spam on every
        # tick) and only if the operator opted in.
        if device.get("alert_enabled"):
            fired = _maybe_fire_alert(
                device=device,
                prev_status=prev_status,
                new_status=new_status,
                latency_ms=latency_ms,
            )
            if fired:
                summary["alerts"] += 1

    # Sprint 5 — sweep expired remote-access sessions in the same
    # tick. Cheap when no expirations are pending (one indexed
    # `WHERE status='active' AND expires_at <= now` query).
    try:
        from . import remote_device_access
        summary["expired_sessions"] = remote_device_access.sweep_expired(
            _load_nas_for_router,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("[net-monitor] session sweep failed")
        summary["expired_sessions"] = 0

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    _LOG.info(
        "[net-monitor] tick scanned=%d up=%d down=%d alerts=%d "
        "expired=%d in %.0fms",
        summary["scanned"], summary["up"], summary["down"],
        summary["alerts"], summary["expired_sessions"],
        elapsed_ms,
    )
    return summary


def _load_nas_for_router(router_id: int) -> dict | None:
    """nas-dict loader injected into remote_device_access.sweep_expired
    so that service stays free of nas_repo imports. Returns the
    dict shape MikrotikClient expects, or None when the router
    has been deleted under us."""
    from ..db.repos import nas_repo
    # The sweep walks distinct tenants — we don't have one here.
    # `get_nas` without tenant scoping isn't exposed, so do the
    # right thing: scan every tenant's view. For a small fleet
    # this is fine; if it grows we'd add an admin-scope helper.
    from ..db.connection import db
    cur = db().execute(
        "SELECT tenant_id FROM nas_devices WHERE id = ?",
        (int(router_id),),
    )
    r = cur.fetchone()
    if not r:
        return None
    nas_dc = nas_repo.get_nas(int(r["tenant_id"]), int(router_id))
    if not nas_dc:
        return None
    return {
        "id":              nas_dc.id,
        "tenant_id":       nas_dc.tenant_id,
        "name":            nas_dc.name,
        "address":         nas_dc.address,
        "api_port":        nas_dc.api_port,
        "api_user":        nas_dc.api_user,
        "api_password":    nas_dc.api_password,
        "api_use_tls":     nas_dc.api_use_tls,
        "api_timeout_sec": getattr(nas_dc, "api_timeout_sec", 3) or 3,
    }


# ─── internals ─────────────────────────────────────────────────


def _all_watched() -> list[dict]:
    """Every watched device across every tenant. Walks the
    distinct-tenant set so we honour each tenant's scope, but
    aggregates for the worker so we only hit the DB once per
    sweep regardless of tenant count."""
    # Cheap two-step: (1) gather distinct tenant_ids, (2) call
    # the repo's tenant-scoped list per id. Keeps the repo's
    # tenant-isolation invariant intact instead of bypassing it.
    from ..db.connection import db
    cur = db().execute(
        "SELECT DISTINCT tenant_id FROM network_devices "
        "WHERE watch_enabled = 1"
    )
    out: list[dict] = []
    for r in cur.fetchall():
        out.extend(network_devices_repo.list_for_tenant(
            int(r["tenant_id"]), watch_only=True,
        ))
    return out


def _probe(device: dict) -> tuple[str, Optional[float], str]:
    """Same TCP-connect probe as the manual «فحص الآن» button.
    Returns (status, latency_ms, error_message).
    A device with no IP gets short-circuited to ('unknown', None,
    reason) so we don't waste a syscall on a blank target.
    """
    ip = (device.get("ip_address") or "").strip()
    port = int(device.get("management_port") or 80)
    if not ip:
        return "unknown", None, "no ip_address configured"
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((ip, port), timeout=TCP_TIMEOUT_SEC)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return "up", round(elapsed_ms, 1), ""
    except socket.timeout:
        return "down", None, "timeout"
    except (ConnectionRefusedError, OSError) as e:
        return "down", None, str(e)[:160]
    finally:
        if sock is not None:
            try: sock.close()
            except OSError: pass


def _maybe_fire_alert(
    *,
    device: dict,
    prev_status: str,
    new_status: str,
    latency_ms: Optional[float],
) -> bool:
    """Decide whether to fire an alert and dispatch it. Returns
    True if anything was actually sent downstream (not skipped
    by cooldown).

    Event derivation:
      prev=up   → new=down      ⇒ device_down
      prev=down → new=up        ⇒ device_up
      new=up, latency>threshold ⇒ device_high_latency
    """
    events: list[tuple[str, str]] = []  # (event_type, message)

    name = device.get("name") or f"#{device.get('id')}"
    ip = device.get("ip_address") or ""
    iso = _now_local_iso()
    is_critical = bool(device.get("is_critical"))
    flag = "⚠️" if not is_critical else "🚨"

    # Transition events
    if prev_status != "down" and new_status == "down":
        events.append((
            "device_down",
            f"{flag} <b>انقطع الاتصال</b> مع «{name}»\n"
            f"IP: <code>{ip}</code>\n"
            f"وقت الفحص: <code>{iso}</code>",
        ))
    elif prev_status == "down" and new_status == "up":
        latency_str = (f"{latency_ms:.1f} ms"
                       if latency_ms is not None else "—")
        events.append((
            "device_up",
            f"✅ <b>عاد الاتصال</b> مع «{name}»\n"
            f"IP: <code>{ip}</code>\n"
            f"البنج الآن: <code>{latency_str}</code>",
        ))

    # High-latency event (additive — can fire even on the same
    # tick as a device_up if the device returned but is slow).
    if (new_status == "up"
            and latency_ms is not None
            and latency_ms > HIGH_LATENCY_MS):
        events.append((
            "device_high_latency",
            f"🐌 <b>البنج مرتفع</b> على «{name}»\n"
            f"IP: <code>{ip}</code>\n"
            f"البنج الحالي: <code>{latency_ms:.1f} ms</code>\n"
            f"الحدّ المعتاد: <code>{HIGH_LATENCY_MS:.0f} ms</code>",
        ))

    fired_anything = False
    for event_type, message in events:
        delivered = _fire_with_cooldown(
            tenant_id=int(device["tenant_id"]),
            device_id=int(device["id"]),
            event_type=event_type,
            message=message,
        )
        if delivered:
            fired_anything = True
        # Phase 3 — also route the alert through the event-driven
        # notifications engine so the operator's SMS/WhatsApp channels can
        # fire for the same network event. The existing Telegram path above
        # is untouched; this is purely additive and fully isolated (a notify
        # failure can never break the monitor tick).
        _notify_network_event(
            tenant_id=int(device["tenant_id"]),
            event_type=event_type,
            device=device,
            latency_ms=latency_ms,
        )
    return fired_anything


# Monitor's internal event types → notifications-engine keys.
_NET_EVENT_TO_NOTIF: dict[str, str] = {
    "device_down": "router_down",
    "device_up": "router_up",
    "device_high_latency": "network_high_latency",
}


def _notify_network_event(
    *,
    tenant_id: int,
    event_type: str,
    device: dict,
    latency_ms: Optional[float],
) -> None:
    """Fire the matching notify_event for a network alert. Never raises.

    Builds a small device-flavoured context ({device},{ip},{time},{latency})
    consumed by the network event templates. The engine itself decides whether
    the rule is enabled and which channels to use — this only hands it the data.
    """
    notif_key = _NET_EVENT_TO_NOTIF.get(event_type)
    if not notif_key:
        return
    try:
        from . import notifications_engine as ne

        latency_str = (f"{latency_ms:.1f} ms" if latency_ms is not None else "—")
        ne.notify_event(
            notif_key,
            tenant_id=int(tenant_id),
            subscriber=None,
            context={
                "device": device.get("name") or f"#{device.get('id')}",
                "ip": device.get("ip_address") or "",
                "time": _now_local_iso(),
                "latency": latency_str,
            },
        )
    except Exception:  # noqa: BLE001 — monitor must never break on a notify
        _LOG.debug("[net-monitor] notify_event fan-out failed (%s)", event_type)


def _fire_with_cooldown(
    *, tenant_id: int, device_id: int,
    event_type: str, message: str,
) -> bool:
    """Cooldown gate + actual dispatch. Always writes a row to
    network_device_alerts (sent/skipped/failed) so the audit
    trail captures the decision. Returns True only on `sent`."""
    cooldown = COOLDOWN_SEC.get(event_type, 0)
    last = network_device_checks_repo.last_alert_at(device_id, event_type)
    if last and cooldown > 0:
        # Compare timestamps directly — `last` is ISO 8601 UTC.
        if _seconds_since_iso(last) < cooldown:
            network_device_checks_repo.record_alert(
                tenant_id=tenant_id, device_id=device_id,
                event_type=event_type, delivery="skipped",
                message="cooldown",
            )
            return False

    # Past the cooldown — attempt downstream dispatch.
    if not tenant_telegram_settings_repo.is_configured(tenant_id):
        # No Telegram set up; log the fire so the operator can see
        # alerts WOULD have gone out, then move on.
        network_device_checks_repo.record_alert(
            tenant_id=tenant_id, device_id=device_id,
            event_type=event_type, delivery="skipped",
            message="telegram not configured",
        )
        return False

    ok, err = telegram_notifier.send_to_tenant(tenant_id, message)
    delivery = "sent" if ok else "failed"
    network_device_checks_repo.record_alert(
        tenant_id=tenant_id, device_id=device_id,
        event_type=event_type, delivery=delivery,
        message=(message if ok else f"{message}\n\n[error] {err}"),
    )
    return ok


# ─── tiny time helpers ─────────────────────────────────────────


def _now_local_iso() -> str:
    """Local-ish wall clock for alert message bodies. We don't
    care about timezone precision here — operators read these
    in their phone's locale, the message is human-facing only.
    Stored DB timestamps go through `now_iso()` separately."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _seconds_since_iso(iso_str: str) -> float:
    """How long ago was `iso_str`? Used by the cooldown gate.
    Returns a huge number on parse failure so the caller treats
    the row as «old enough to re-fire»."""
    try:
        # SQLite's `datetime('now')` writes 'YYYY-MM-DD HH:MM:SS'.
        dt = datetime.strptime(iso_str.strip()[:19], "%Y-%m-%d %H:%M:%S")
        # Treat as naive-UTC (same as SQLite default) for comparison.
        dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds())
    except Exception:  # noqa: BLE001
        return 1e9
