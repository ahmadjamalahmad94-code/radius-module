"""events_publisher — S11.1 realtime event abstraction.

In-process pub/sub. Future tracks (SSE endpoint, WebSocket
worker, browser progressive enhancement) consume `subscribe()`
to get a generator of events. For now nothing reads — the
publisher just stores events in a bounded ring buffer + emits
to in-process subscribers.

What this is NOT:
  - No WebSocket / no Redis. The shape is queue-ready so a
    future commit can swap the in-process buffer for a real
    broker.
  - No persistence. Restarts lose events. That's fine — the
    audit log is the durable record; events are for live UI.

Safety:
  - Every payload goes through the shared _redact helper from
    jobs_repo. A leaked password in an event payload can't
    reach a subscriber.
  - The buffer is bounded (default 500) so a publisher loop
    can't OOM the process.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..db.repos.jobs_repo import _redact


# ─── Event taxonomy (stable identifiers) ──────────────────────


EVENT_ROUTER_SNAPSHOT     = "router.snapshot.updated"
EVENT_JOB_PROGRESS        = "job.progress.updated"
EVENT_ALERT_OPENED        = "alert.opened"
EVENT_ALERT_RESOLVED      = "alert.resolved"
EVENT_BACKUP_COMPLETED    = "backup.completed"
EVENT_DEPLOY_COMPLETED    = "deploy.completed"


KNOWN_EVENT_TYPES = frozenset({
    EVENT_ROUTER_SNAPSHOT,
    EVENT_JOB_PROGRESS,
    EVENT_ALERT_OPENED,
    EVENT_ALERT_RESOLVED,
    EVENT_BACKUP_COMPLETED,
    EVENT_DEPLOY_COMPLETED,
})


# ─── Event shape ──────────────────────────────────────────────


@dataclass(frozen=True)
class Event:
    type: str
    target_type: str
    target_id: str
    tenant_id: int
    payload: dict
    created_at: str = field(default_factory=lambda:
                            datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "tenant_id": self.tenant_id,
            "payload": self.payload,
            "created_at": self.created_at,
        }


# ─── Bounded ring buffer + subscribers ────────────────────────


_BUFFER_MAX = 500
_buffer: "deque[Event]" = deque(maxlen=_BUFFER_MAX)
_subscribers: "list[Callable[[Event], None]]" = []
_lock = threading.RLock()


def publish_event(
    *, type: str, target_type: str, target_id: str | int,
    tenant_id: int = 1,
    payload: dict | None = None,
) -> Event:
    """Append an event to the buffer + notify subscribers.

    Returns the event. Payload is redacted at the boundary —
    callers can pass dicts containing API passwords / radius
    secrets and trust they won't reach a subscriber.
    """
    if not type:
        raise ValueError("event type required")
    safe = _redact(dict(payload or {}))
    ev = Event(
        type=str(type), target_type=str(target_type),
        target_id=str(target_id),
        tenant_id=int(tenant_id),
        payload=safe,
    )
    with _lock:
        _buffer.append(ev)
        # Snapshot the subscriber list so callbacks raising
        # exceptions can't corrupt iteration.
        subs = list(_subscribers)
    for cb in subs:
        try:
            cb(ev)
        except Exception:  # noqa: BLE001
            # A bad subscriber can't poison the publisher.
            # Future commits can log this; for now swallow.
            pass
    return ev


def subscribe(callback: Callable[[Event], None]) -> Callable[[], None]:
    """Register a callable that gets each new Event. Returns an
    `unsubscribe` thunk."""
    with _lock:
        _subscribers.append(callback)

    def _unsubscribe() -> None:
        with _lock:
            try:
                _subscribers.remove(callback)
            except ValueError:
                pass
    return _unsubscribe


def recent(
    *, tenant_id: int | None = None,
    type: str | None = None,
    limit: int = 100,
) -> list[Event]:
    """Snapshot of the buffer, newest-first, optionally filtered.
    Useful for an `/admin/radius/events` debug page or for an
    SSE endpoint that wants to backfill on connection."""
    with _lock:
        items = list(_buffer)
    items.reverse()
    if tenant_id is not None:
        items = [e for e in items if e.tenant_id == int(tenant_id)]
    if type:
        items = [e for e in items if e.type == type]
    return items[:max(0, int(limit))]


def _reset_for_tests() -> None:
    with _lock:
        _buffer.clear()
        _subscribers.clear()


__all__ = [
    "EVENT_ROUTER_SNAPSHOT", "EVENT_JOB_PROGRESS",
    "EVENT_ALERT_OPENED", "EVENT_ALERT_RESOLVED",
    "EVENT_BACKUP_COMPLETED", "EVENT_DEPLOY_COMPLETED",
    "KNOWN_EVENT_TYPES",
    "Event",
    "publish_event", "subscribe", "recent",
]
