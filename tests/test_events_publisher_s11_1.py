"""S11.1 — Realtime event publisher contract."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_pub():
    from app.radius.services import events_publisher as ep
    ep._reset_for_tests()
    yield
    ep._reset_for_tests()


def test_publish_returns_event_with_redacted_payload():
    from app.radius.services.events_publisher import (
        publish_event, EVENT_BACKUP_COMPLETED,
    )
    ev = publish_event(
        type=EVENT_BACKUP_COMPLETED,
        target_type="mikrotik_nas", target_id=42,
        tenant_id=1,
        payload={"router_api_password": "LEAK", "ok": True},
    )
    assert ev.payload["router_api_password"] == "***"
    assert ev.payload["ok"] is True


def test_publish_rejects_empty_type():
    from app.radius.services.events_publisher import publish_event
    with pytest.raises(ValueError):
        publish_event(type="", target_type="x", target_id="1")


def test_subscribers_get_called_in_order():
    from app.radius.services.events_publisher import (
        publish_event, subscribe,
    )
    seen = []
    unsub_a = subscribe(lambda e: seen.append(("a", e.type)))
    unsub_b = subscribe(lambda e: seen.append(("b", e.type)))
    publish_event(type="router.snapshot.updated",
                   target_type="t", target_id="1")
    assert seen == [("a", "router.snapshot.updated"),
                    ("b", "router.snapshot.updated")]
    unsub_a()
    unsub_b()


def test_subscriber_exception_does_not_block_others():
    from app.radius.services.events_publisher import (
        publish_event, subscribe,
    )
    calls = []

    def _bad(e):
        raise RuntimeError("subscriber boom")
    def _good(e):
        calls.append(e.type)
    subscribe(_bad)
    subscribe(_good)
    publish_event(type="alert.opened",
                   target_type="t", target_id="1")
    assert calls == ["alert.opened"]


def test_unsubscribe_stops_receiving():
    from app.radius.services.events_publisher import (
        publish_event, subscribe,
    )
    calls = []
    unsub = subscribe(lambda e: calls.append(e.type))
    publish_event(type="job.progress.updated",
                   target_type="t", target_id="1")
    unsub()
    publish_event(type="job.progress.updated",
                   target_type="t", target_id="1")
    assert calls == ["job.progress.updated"]


def test_recent_returns_newest_first_and_filters():
    from app.radius.services.events_publisher import (
        publish_event, recent, EVENT_ALERT_OPENED,
        EVENT_BACKUP_COMPLETED,
    )
    publish_event(type=EVENT_ALERT_OPENED, target_type="t",
                   target_id="1", tenant_id=1)
    publish_event(type=EVENT_BACKUP_COMPLETED, target_type="t",
                   target_id="1", tenant_id=2)
    publish_event(type=EVENT_ALERT_OPENED, target_type="t",
                   target_id="2", tenant_id=1)
    all_recent = recent(tenant_id=1)
    # Two events for tenant_id=1, newest first.
    assert [e.target_id for e in all_recent] == ["2", "1"]
    only_alerts = recent(type=EVENT_ALERT_OPENED)
    assert all(e.type == EVENT_ALERT_OPENED for e in only_alerts)


def test_buffer_is_bounded():
    """500-entry ring buffer prevents OOM from runaway loops."""
    from app.radius.services.events_publisher import (
        publish_event, recent, _buffer,
    )
    for i in range(600):
        publish_event(type="job.progress.updated",
                       target_type="t", target_id=str(i))
    assert len(_buffer) == 500
    # The OLDEST events are dropped; recent() shows newest.
    rows = recent(limit=10)
    assert rows[0].target_id == "599"


def test_event_taxonomy_is_stable():
    """Pin the stable identifiers — never rename, only add."""
    from app.radius.services.events_publisher import (
        KNOWN_EVENT_TYPES,
    )
    assert KNOWN_EVENT_TYPES == {
        "router.snapshot.updated",
        "job.progress.updated",
        "alert.opened",
        "alert.resolved",
        "backup.completed",
        "deploy.completed",
    }
