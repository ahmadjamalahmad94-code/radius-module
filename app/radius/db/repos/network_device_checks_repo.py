"""network_device_checks + network_device_alerts repo (Sprint 2).

Append-only ping history + alert event log. The cron worker
(`services/network_device_monitor.py`) is the only writer;
read paths feed the dashboard list page (sparklines, last-N
view) and the cooldown check in the notifier.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


# ── checks (ping history) ──────────────────────────────────────


def record_check(
    *,
    device_id: int,
    status: str,
    latency_ms: Optional[float] = None,
    error_message: str = "",
    source: str = "backend_ping",
    checked_at: Optional[str] = None,
) -> int:
    """Append one probe result. Returns the inserted row id.

    The cron worker calls this AND
    `network_devices_repo.set_last_check()` so the device row
    and the history table stay in sync — both use the same
    `checked_at` timestamp.
    """
    ts = checked_at or now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO network_device_checks ("
            "  device_id, checked_at, status, latency_ms,"
            "  error_message, source"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(device_id),
                ts,
                status,
                float(latency_ms) if latency_ms is not None else None,
                str(error_message or "")[:200],
                source,
            ),
        )
        return int(cur.lastrowid)


def recent_checks(device_id: int, limit: int = 60) -> list[dict]:
    """Most-recent N rows for a device, newest first. Used by the
    dashboard sparkline + diagnostics panel. Limit caps at 500
    to keep the response cheap even for chatty pollers."""
    limit = max(1, min(int(limit or 60), 500))
    cur = db().execute(
        "SELECT id, checked_at, status, latency_ms,"
        "       error_message, source "
        "FROM network_device_checks "
        "WHERE device_id = ? "
        "ORDER BY checked_at DESC "
        "LIMIT ?",
        (int(device_id), limit),
    )
    return [
        {
            "id":            int(r["id"]),
            "checked_at":    r["checked_at"] or "",
            "status":        r["status"] or "unknown",
            "latency_ms":    float(r["latency_ms"]) if r["latency_ms"] is not None else None,
            "error_message": r["error_message"] or "",
            "source":        r["source"] or "",
        }
        for r in cur.fetchall()
    ]


# ── alerts (dedup + audit) ─────────────────────────────────────


def record_alert(
    *,
    tenant_id: int,
    device_id: int,
    event_type: str,
    delivery: str = "sent",
    message: str = "",
    fired_at: Optional[str] = None,
) -> int:
    """Log one alert fire. `delivery` is 'sent' on real
    downstream delivery, 'skipped' on cooldown-dedup, 'failed'
    when Telegram/etc returned an error. Cooldown lookups
    consider ALL three so a dedup-skipped fire still resets
    the cooldown clock."""
    ts = fired_at or now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO network_device_alerts ("
            "  tenant_id, device_id, event_type, fired_at,"
            "  delivery, message"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(tenant_id),
                int(device_id),
                str(event_type),
                ts,
                str(delivery),
                str(message or "")[:1000],
            ),
        )
        return int(cur.lastrowid)


def last_alert_at(device_id: int, event_type: str) -> Optional[str]:
    """ISO of the most-recent alert of this (device, type), or
    None if we've never fired one. Cooldown check uses this."""
    cur = db().execute(
        "SELECT fired_at FROM network_device_alerts "
        "WHERE device_id = ? AND event_type = ? "
        "ORDER BY fired_at DESC LIMIT 1",
        (int(device_id), str(event_type)),
    )
    r = cur.fetchone()
    return r["fired_at"] if r else None
