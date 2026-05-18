"""CardsService — توليد الكروت + ربطها بـ adapter كحسابات."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..core.constants import AUDIT_ACTION_BATCH_GENERATE, AUDIT_ACTION_REVOKE, USER_TYPE_CARD
from ..core.errors import RadiusValidationError
from ..core.types import Card, CardBatch, Subscriber
from ..integration.adapter import RadiusAdapter
from ..stores.cards_store import CardsStore
from .audit import RadiusAuditService


class CardsService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit
        self._store = CardsStore.instance()

    def list_batches(self, *, limit: int = 100, offset: int = 0):
        return self._store.list_batches(limit=limit, offset=offset)

    def list_cards(self, **kw):
        return self._store.list_cards(**kw)

    def stats(self) -> dict:
        return self._store.stats()

    def generate_batch(
        self,
        *,
        actor: str,
        plan_id: int,
        count: int,
        username_prefix: str = "",
        username_length: int = 8,
        password_length: int = 6,
        notes: str = "",
    ) -> tuple[CardBatch, list[Card]]:
        if count <= 0 or count > 2000:
            raise RadiusValidationError("count بين 1 و 2000")
        if not plan_id:
            raise RadiusValidationError("plan_id مطلوب")

        plan = self._adapter.get_profile(plan_id)
        expire = None
        if plan.validity_days:
            expire = datetime.utcnow() + timedelta(days=plan.validity_days)

        batch = self._store.create_batch(CardBatch(
            id=None, batch_code="", plan_id=plan_id, count=count,
            username_prefix=username_prefix, username_length=username_length,
            password_length=password_length, notes=notes, created_by=actor,
        ))
        cards = self._store.generate_cards_for_batch(
            batch_id=batch.id, plan_id=plan_id, count_to_make=count,
            username_prefix=username_prefix, username_length=username_length,
            password_length=password_length, expire_at=expire,
        )
        # سجّل كل بطاقة كحساب RADIUS (subscriber من نوع card)
        for c in cards:
            self._adapter.upsert_account(Subscriber(
                id=None, username=c.username, password=c.password,
                user_type=USER_TYPE_CARD, plan_id=plan_id,
                expire_at=c.expire_at, card_batch_id=batch.id, created_by=actor,
            ))
        self._audit.record(
            actor=actor, action=AUDIT_ACTION_BATCH_GENERATE,
            target_type="card_batch", target_id=str(batch.id),
            payload={"plan_id": plan_id, "count": count, "batch_code": batch.batch_code},
        )
        return self._store.get_batch(batch.id), cards

    def revoke_card(self, *, actor: str, card_id: int) -> None:
        self._store.revoke(card_id)
        self._audit.record(actor=actor, action=AUDIT_ACTION_REVOKE,
                           target_type="card", target_id=str(card_id))


def get_cards_service() -> CardsService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return CardsService(get_radius_adapter(), audit=get_audit_service())
