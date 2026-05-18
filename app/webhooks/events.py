"""
أنواع أحداث الـ webhooks المدعومة + شكل الـ payload لكل واحد.

كل event ثابت بـ name + schema لـ data. أي تغيير = event جديد + رقم نسخة.
"""
from __future__ import annotations

EVENT_TYPES: tuple[str, ...] = (
    # accounts
    "account.created",
    "account.updated",
    "account.disabled",
    "account.expired",
    # cards
    "card.generated",
    "card.consumed",
    # sessions
    "session.started",
    "session.stopped",
    "session.disconnected",
    # quota
    "quota.threshold",
    # infra
    "nas.unreachable",
    # meta
    "webhook.test",
)


def is_known(event: str) -> bool:
    return event in EVENT_TYPES
