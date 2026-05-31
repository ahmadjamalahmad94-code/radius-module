"""
dispatcher — يضع كل event في queue الـ DB لكل الاشتراكات النشطة للـ tenant.
الـ queue worker يلتقطها لاحقًا.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from app.radius.db.repos import webhooks_repo

from .events import is_known

_LOG = logging.getLogger(__name__)
_API_VERSION = "v1"


def _envelope(event: str, data: dict[str, Any]) -> dict:
    return {
        "event": event,
        "event_id": "ev_" + uuid.uuid4().hex[:16],
        "occurred_at": datetime.utcnow().isoformat() + "Z",
        "data": data,
        "version": _API_VERSION,
    }


def dispatch_event(event: str, data: dict[str, Any], *, tenant_id: int = 1) -> str:
    """يضع الـ event في queue (DB) لكل subscription نشط للـ tenant."""
    env = _envelope(event, data)
    if not is_known(event):
        _LOG.warning("dispatching unknown event %r — لن يُسجَّل", event)
        return env["event_id"]
    subs = webhooks_repo.list_subs(tenant_id)
    for s in subs:
        if not s.enabled: continue
        if s.enabled_events and event not in s.enabled_events: continue
        webhooks_repo.enqueue(tenant_id, s.id, event=event,
                              event_id=env["event_id"], payload=env)
    # Phase 3 — fan the same business event out to the event-driven
    # notifications engine (SMS/WhatsApp/Telegram per the operator's rules).
    # Fire-and-forget + fully isolated: a notification failure must NEVER break
    # webhook dispatch, so any error is swallowed here.
    try:
        _notify_from_event(event, data, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        _LOG.debug("notify_event fan-out failed for %r — webhook dispatch unaffected", event)
    return env["event_id"]


# Map the clean, subscriber-bearing webhook events → notifications-engine keys.
# Conservative on purpose: only events whose payload reliably identifies a
# subscriber are mapped. account.updated is special-cased on status below.
_WEBHOOK_TO_NOTIF: dict[str, str] = {
    "account.created": "subscriber_created",
    "account.expired": "subscriber_expired",
}


def _notify_from_event(event: str, data: dict[str, Any], *, tenant_id: int) -> None:
    """Translate a webhook event into a ``notify_event`` call, when it maps.

    Only fires for the small set of clean account.* events that carry a
    ``username`` (so the engine can resolve the subscriber + render real
    variables). Everything else is a quiet no-op here.
    """
    notif_key = _WEBHOOK_TO_NOTIF.get(event)
    # account.updated with status=active → "subscriber_activated".
    if notif_key is None and event == "account.updated":
        status = str((data or {}).get("status") or "").strip().lower()
        if status in ("active", "enabled"):
            notif_key = "subscriber_activated"
    if not notif_key:
        return

    username = str((data or {}).get("username") or "").strip()
    if not username:
        return

    from app.radius.services import notifications_engine as ne

    subscriber = ne.find_subscriber(tenant_id, username=username)
    ne.notify_event(notif_key, tenant_id=tenant_id, subscriber=subscriber)
