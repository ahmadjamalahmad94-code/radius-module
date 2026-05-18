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
    return env["event_id"]
