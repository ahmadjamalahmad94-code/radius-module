"""Notification timeline — one unified view over the messaging subsystem.

Three buckets for the operator's «سجل الإشعارات» page:

  • sent     — delivered channel messages (message_deliveries.status sent/…)
  • pending  — queued/skipped channel messages (waiting on a provider)
  • failed   — provider failures (with the error)
  • scheduled— messages that WILL fire within the next week. There is no table
               of future outbound messages, so this is DERIVED from the
               near-expiry (dunning) reminder: active subscribers whose
               subscription expires within the window get a synthesized row
               (recipient, channels, reason, the date the reminder will fire).

Read-only. Never raises for a single bad row — the page must always render.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.system_config import to_local
from .notification_campaigns import NotificationCampaignService

# Delivery statuses grouped into the three display buckets.
_SENT = ("sent", "delivered", "read")
_FAILED = ("failed", "error")
_PENDING = ("queued", "pending", "skipped")

# Fallback human reason from the stored notification_type when no explicit
# reason/event was recorded on the row (older sends).
_TYPE_REASON_AR = {
    "manual": "رسالة يدوية",
    "campaign": "حملة",
}


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json")
    if not raw:
        return {}
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def _reason_label(row: dict[str, Any]) -> str:
    """Best-effort «السبب» for a delivery row.

    Priority: explicit reason stored in metadata → known event key → the
    notification_type label → subject → em-dash.
    """
    meta = _meta(row)
    reason = str(meta.get("reason") or "").strip()
    if reason:
        return reason
    # event key → its Arabic label from the engine registry (lazy import).
    event = str(meta.get("event") or "").strip()
    if event:
        try:
            from .notifications_engine import EVENTS
            ev = EVENTS.get(event)
            if ev is not None:
                return ev.label
        except Exception:  # noqa: BLE001
            pass
        return event
    ntype = str(row.get("notification_type") or "").strip()
    if ntype in _TYPE_REASON_AR:
        return _TYPE_REASON_AR[ntype]
    subj = str(row.get("subject") or "").strip()
    return subj or "—"


def _delivery_rows(tenant_id: int, *, limit: int) -> list[dict[str, Any]]:
    """message_deliveries ⨝ message_notifications, recipient-resolved, enriched
    with a reason label and a localized «when»."""
    from ..db.connection import db
    from ..db.helpers import row_to_dict

    svc = NotificationCampaignService(tenant_id=int(tenant_id or 1))
    rows = [
        row_to_dict(r)
        for r in db().execute(
            """
            SELECT d.id, d.channel, d.status, d.provider_key, d.error_message,
                   d.recipient_address, d.sent_at, d.created_at AS attempted_at,
                   n.recipient_type, n.recipient_id, n.subject, n.body,
                   n.notification_type, n.metadata_json
            FROM message_deliveries d
            JOIN message_notifications n ON n.id = d.notification_id
            WHERE d.tenant_id = ?
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (int(tenant_id or 1), int(limit)),
        ).fetchall()
    ]
    # Reuse the campaign service's name resolver (subscriber/card_user/…).
    rows = svc._resolve_recipient_names(rows)  # noqa: SLF001 — intentional reuse
    for r in rows:
        r["reason_label"] = _reason_label(r)
        when = r.get("sent_at") or r.get("attempted_at")
        r["when_iso"] = when
        r["when_local"] = to_local(when, tenant_id=tenant_id) if when else ""
    return rows


def _scheduled_near_expiry(tenant_id: int, *, within_days: int) -> dict[str, Any]:
    """Synthesize the upcoming near-expiry reminders for the next ``within_days``.

    Mirrors the dunning worker exactly (``_expiring_subscribers``) so what we
    show is what will actually fire. Each row carries the recipient, the channels
    the reminder will use, and the date it fires (expire_at − days_before,
    clamped to today). Returns ``{enabled, days_before, channels, items}``.
    """
    try:
        from .notifications_engine import load_rule
        from ...workers.dunning_worker import _expiring_subscribers  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return {"enabled": False, "days_before": 0, "channels": [], "items": []}

    rule = load_rule(int(tenant_id or 1), "near_expiry")
    channels = rule.active_channels() if rule else []
    enabled = bool(rule and rule.enabled and channels)
    days_before = int(rule.days_before) if rule else 0
    if not enabled:
        return {"enabled": False, "days_before": days_before,
                "channels": channels, "items": []}

    today = datetime.now(timezone.utc).date()
    items: list[dict[str, Any]] = []
    for sub, days_left in _expiring_subscribers(int(tenant_id or 1), int(within_days)):
        exp = getattr(sub, "expire_at", None)
        # Reminder fires when days_left ≤ days_before → fire date = today +
        # max(0, days_left − days_before). If already inside the window it fires
        # on the next dunning sweep (treated as «قريبًا جدًّا»).
        fire_in = max(0, int(days_left) - days_before)
        fire_date = today + timedelta(days=fire_in)
        exp_str = to_local(exp, tenant_id=tenant_id) if exp else ""
        body = str(rule.template)
        body = body.replace("{days}", str(days_before)).replace(
            "{exp}", (exp_str[:10] if exp_str else ""))
        items.append({
            "recipient_display": (getattr(sub, "full_name", "")
                                  or getattr(sub, "username", "") or "—"),
            "recipient_mobile": getattr(sub, "mobile", "") or "",
            "channels": channels,
            "reason_label": "قرب انتهاء الاشتراك",
            "body": body,
            "expire_at": exp,
            "expire_local": exp_str,
            "days_left": int(days_left),
            "fire_in_days": fire_in,
            "fire_date": fire_date.isoformat(),
            "imminent": int(days_left) <= days_before,
        })
    items.sort(key=lambda it: (it["fire_in_days"], it["days_left"]))
    return {"enabled": True, "days_before": days_before,
            "channels": channels, "items": items}


def build_timeline(
    tenant_id: int,
    *,
    log_limit: int = 300,
    scheduled_within_days: int = 7,
) -> dict[str, Any]:
    """Assemble the full notifications timeline. Never raises."""
    try:
        deliveries = _delivery_rows(tenant_id, limit=log_limit)
    except Exception:  # noqa: BLE001 — the page must still render
        deliveries = []

    sent, pending, failed = [], [], []
    for r in deliveries:
        st = str(r.get("status") or "").lower()
        if st in _SENT:
            sent.append(r)
        elif st in _FAILED:
            failed.append(r)
        else:
            pending.append(r)

    scheduled = _scheduled_near_expiry(tenant_id, within_days=scheduled_within_days)

    return {
        "sent": sent,
        "pending": pending,
        "failed": failed,
        "scheduled": scheduled,
        "counts": {
            "sent": len(sent),
            "pending": len(pending),
            "failed": len(failed),
            "scheduled": len(scheduled["items"]),
        },
        "scheduled_within_days": int(scheduled_within_days),
    }
