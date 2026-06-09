"""device_health_alerts — Phase 5 deduplicated alert dispatch.

Decides whether a device status transition warrants an operator alert and sends
it via the EXISTING notification channels (Telegram direct, or the
notifications engine for the tenant's configured channels). Records every
decision (sent/skipped/failed) in network_device_monitor_alerts so the cooldown
gate and the audit trail both work.

Dedup rules (per device, per alert type):
  • down          — fires only after N consecutive fails; repeat suppressed for
                    COOLDOWN_SEC['down'] (no alert every poll while down).
  • high_latency  — fires after N consecutive high-latency samples; repeat
                    suppressed for COOLDOWN_SEC['high_latency'].
  • recovery      — fires once when a previously-down device returns; its own
                    short cooldown guards against flap double-sends.

`_send` is the single delivery seam (mockable in tests); the state machine is
what Phase 5 actually guarantees.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..db.repos import device_health_repo as repo

_LOG = logging.getLogger(__name__)

# How many consecutive bad samples before the first alert.
DOWN_AFTER_N = 2
HIGH_LATENCY_AFTER_N = 3

# Repeat-suppression window per alert type (seconds).
COOLDOWN_SEC = {
    "down": 5 * 60,
    "high_latency": 15 * 60,
    "recovery": 2 * 60,
}

# Internal alert type → notifications-engine event key.
_ENGINE_KEY = {
    "down": "router_down",
    "recovery": "router_up",
    "high_latency": "network_high_latency",
}


def evaluate_and_dispatch(
    *,
    tenant_id: int,
    device: dict,
    prev_status: str,
    new_status: str,
    latency_ms: Optional[float] = None,
) -> list:
    """Evaluate the transition and dispatch any warranted alerts.

    `device` must be the FRESH row (consecutive counters already updated by the
    poller's set_status). Returns the list of alert types actually sent.
    """
    tid = int(tenant_id)
    device_id = int(device["id"])
    channel = (device.get("alert_channel") or "").strip()
    name = device.get("name") or f"#{device_id}"
    ip = device.get("ip_address") or ""
    now_h = _now_human()
    lat_str = f"{latency_ms} ms" if latency_ms is not None else "—"

    pending: list[tuple[str, str]] = []  # (alert_type, message)

    if new_status in ("down", "timeout") \
            and int(device.get("consecutive_down_count") or 0) >= DOWN_AFTER_N:
        pending.append(("down",
                        f"🚨 انقطع الاتصال مع «{name}»\nالعنوان: {ip}\nالوقت: {now_h}"))

    if prev_status in ("down", "timeout") and new_status == "up":
        pending.append(("recovery",
                        f"✅ عاد الاتصال مع «{name}»\nالعنوان: {ip}\nالبنج: {lat_str}"))

    if new_status == "high_latency" \
            and int(device.get("consecutive_high_latency_count") or 0) >= HIGH_LATENCY_AFTER_N:
        pending.append(("high_latency",
                        f"🐌 ارتفاع البنج على «{name}»\nالعنوان: {ip}\nالبنج الحالي: {lat_str}"))

    fired: list[str] = []
    for alert_type, message in pending:
        dedup_key = f"{device_id}:{alert_type}"
        cooldown = COOLDOWN_SEC.get(alert_type, 0)
        last = repo.last_alert_at(tid, dedup_key)
        if last and cooldown > 0 and _seconds_since(last) < cooldown:
            repo.add_alert(tenant_id=tid, device_id=device_id,
                           alert_type=alert_type, channel=channel or "default",
                           status="skipped", dedup_key=dedup_key,
                           message="cooldown")
            continue
        ok = _send(tid, channel, alert_type, message, device, latency_ms)
        repo.add_alert(tenant_id=tid, device_id=device_id, alert_type=alert_type,
                       channel=channel or "default",
                       status="sent" if ok else "failed",
                       dedup_key=dedup_key, message=message)
        if ok:
            fired.append(alert_type)
    return fired


def _send(tenant_id: int, channel: str, alert_type: str, message: str,
          device: dict, latency_ms: Optional[float]) -> bool:
    """Deliver via EXISTING channels. Returns True on a successful send.

    • channel == 'telegram' → telegram_notifier (operator's tenant chat).
    • otherwise (sms/whatsapp/'' default) → notifications engine, which honours
      the tenant's configured rule + channels for the matching network event.
    Never raises — a delivery failure returns False and is recorded as 'failed'.
    """
    try:
        if channel == "telegram":
            from . import telegram_notifier
            ok, _err = telegram_notifier.send_to_tenant(int(tenant_id), message)
            return bool(ok)

        from . import notifications_engine as ne
        key = _ENGINE_KEY.get(alert_type)
        if not key:
            return False
        lat_str = f"{latency_ms} ms" if latency_ms is not None else "—"
        outcome = ne.notify_event(
            key, tenant_id=int(tenant_id), subscriber=None,
            context={"device": device.get("name") or f"#{device.get('id')}",
                     "ip": device.get("ip_address") or "",
                     "time": _now_human(), "latency": lat_str})
        # fired + at least one channel succeeded.
        return bool(getattr(outcome, "fired", False)
                    and any((getattr(outcome, "sent", {}) or {}).values()))
    except Exception:  # noqa: BLE001 — alerting must never break the poller
        _LOG.debug("[device-health] alert send failed (%s)", alert_type)
        return False


# ── time helpers (monkeypatchable in tests) ────────────────────

def _now_human() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _seconds_since(iso_str: str) -> float:
    """Seconds since an ISO-8601 timestamp (now_iso() writes '…Z'). Returns a
    huge number on parse failure so the caller treats the row as old enough."""
    try:
        s = str(iso_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:  # noqa: BLE001
        return 1e9
