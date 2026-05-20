"""CardsService — توليد الكروت + ربطها بـ adapter كحسابات."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..core.constants import (
    AUDIT_ACTION_UPDATE,
    AUDIT_ACTION_BATCH_ARCHIVE,
    AUDIT_ACTION_BATCH_GENERATE,
    AUDIT_ACTION_REVOKE,
    USER_TYPE_CARD,
)
from ..core.errors import RadiusValidationError
from ..core.types import Card, CardBatch, Subscriber
from ..db.repos import cards_repo
from ..integration.adapter import RadiusAdapter
from ..stores.cards_store import CardsStore
from .audit import RadiusAuditService
from .audit_events import roadmap_audit_payload


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

    def batch_operational_summary(self, batch_id: int) -> dict | None:
        return cards_repo.batch_operational_summary(self._store_tenant_id(), batch_id)

    def _store_tenant_id(self) -> int:
        from ..core.tenant import DEFAULT_TENANT_ID
        try:
            from flask import g
            return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
        except (ImportError, RuntimeError):
            return DEFAULT_TENANT_ID

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool(value) -> bool:
        return value in (True, 1, "1", "true", "yes", "on")

    def generate_batch(
        self,
        *,
        actor: str,
        plan_id: int,
        count: int,
        # ── خيارات RM-H4 (كلها optional عشان توافق calls قديمة) ──
        username_prefix: str = "",
        username_suffix: str = "",
        username_length: int = 8,
        password_length: int = 6,
        password_charset: str = "digits",
        password_generation_type: str = "medium",
        include_batch_number: bool = False,
        starts_with_or_ends_with: str = "",
        prefix_or_suffix_value: str = "",
        random_generation_enabled: bool = True,
        time_value: int = 0,
        time_unit: str = "days",
        device_count: int = 1,
        duration_mode: str = "time_unit",
        validity_after_first_login_days: int = 0,
        count_by_seconds: bool = False,
        count_from_first_connect: bool = True,
        on_quota_exhaust: str = "stop",
        auto_renew_after_first_use: bool = False,
        transfer_to_student_status_on_connect: bool = False,
        close_user_session_on_disconnect: bool = False,
        allow_entry_by_previous_card_palestine: bool = False,
        switch_to_mac_on_connect: bool = False,
        lock_to_mac_on_close: bool = False,
        phone_only_login: bool = False,
        price_per_card: float = 0.0,
        price_bulk: float = 0.0,
        total_price: float = 0.0,
        total_quota_mb: int = 0,
        package_name: str = "",
        service_name: str = "",
        manager_id: int = 0,
        notes: str = "",
        metadata: str = "{}",
    ) -> tuple[CardBatch, list[Card]]:
        if count <= 0 or count > 2000:
            raise RadiusValidationError("count بين 1 و 2000")
        if not plan_id:
            raise RadiusValidationError("plan_id مطلوب")

        plan = self._adapter.get_profile(plan_id)
        expire = None
        # حساب الـ expire: time_value/time_unit يتقدم على plan.validity_days لو مُحدَّد
        if time_value and time_unit and duration_mode == "time_unit":
            if time_unit == "days":
                expire = datetime.utcnow() + timedelta(days=time_value)
            elif time_unit == "hours":
                expire = datetime.utcnow() + timedelta(hours=time_value)
            elif time_unit == "minutes":
                expire = datetime.utcnow() + timedelta(minutes=time_value)
        elif plan.validity_days:
            expire = datetime.utcnow() + timedelta(days=plan.validity_days)

        # تحويل password_generation_type إلى password_charset لو الـ caller ما حدّد charset مخصص
        if password_generation_type and password_charset == "digits":
            pgt_map = {
                "digits": "digits", "weak": "alpha",
                "medium": "mixed", "strong": "mixed",
            }
            password_charset = pgt_map.get(password_generation_type, "mixed")

        # ── RM-QA: H4 fix — apply starts_with_or_ends_with + prefix_or_suffix_value ──
        # هذه الحقول الجديدة في H4 كانت تُحفظ في DB لكن لم تُطبَّق فعلًا على usernames.
        # نطويها فوق username_prefix/username_suffix legacy قبل تمريرها للمولّد.
        if prefix_or_suffix_value:
            if starts_with_or_ends_with == "prefix":
                username_prefix = (prefix_or_suffix_value or "") + (username_prefix or "")
            elif starts_with_or_ends_with == "suffix":
                username_suffix = (username_suffix or "") + (prefix_or_suffix_value or "")

        batch = self._store.create_batch(CardBatch(
            id=None, batch_code="", plan_id=plan_id, count=count,
            package_name=package_name,
            username_prefix=username_prefix, username_suffix=username_suffix,
            username_length=username_length,
            include_batch_number=include_batch_number,
            password_length=password_length, password_charset=password_charset,
            expire_at=expire,
            validity_after_first_login_days=validity_after_first_login_days,
            count_by_seconds=count_by_seconds, count_from_first_connect=count_from_first_connect,
            on_quota_exhaust=on_quota_exhaust,
            switch_to_mac_on_connect=switch_to_mac_on_connect,
            lock_to_mac_on_close=lock_to_mac_on_close, phone_only_login=phone_only_login,
            service_name=service_name, notes=notes, manager_id=manager_id, created_by=actor,
            price_per_card=price_per_card, price_bulk=price_bulk, total_quota_mb=total_quota_mb,
            # RM-H4
            password_generation_type=password_generation_type,
            random_generation_enabled=random_generation_enabled,
            starts_with_or_ends_with=starts_with_or_ends_with,
            prefix_or_suffix_value=prefix_or_suffix_value,
            time_value=time_value, time_unit=time_unit,
            device_count=device_count, duration_mode=duration_mode,
            auto_renew_after_first_use=auto_renew_after_first_use,
            transfer_to_student_status_on_connect=transfer_to_student_status_on_connect,
            close_user_session_on_disconnect=close_user_session_on_disconnect,
            allow_entry_by_previous_card_palestine=allow_entry_by_previous_card_palestine,
            total_price=total_price, metadata=metadata,
        ))
        cards = self._store.generate_cards_for_batch(
            batch_id=batch.id, plan_id=plan_id, count_to_make=count,
            username_prefix=username_prefix, username_suffix=username_suffix,
            username_length=username_length,
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

    def update_batch(self, *, actor: str, batch_id: int, data: dict) -> CardBatch:
        batch = self._store.get_batch(batch_id)
        if not batch:
            raise RadiusValidationError("دفعة الكروت غير موجودة")

        changes: dict = {}
        text_fields = (
            "package_name",
            "username_prefix",
            "username_suffix",
            "password_charset",
            "starts_with_or_ends_with",
            "prefix_or_suffix_value",
            "time_unit",
            "duration_mode",
            "on_quota_exhaust",
            "service_name",
            "notes",
            "status",
            "password_generation_type",
            "metadata",
            "assigned_to",
        )
        int_fields = (
            "plan_id",
            "count",
            "total_quota_mb",
            "username_length",
            "password_length",
            "validity_after_first_login_days",
            "manager_id",
            "time_value",
            "device_count",
            "distributor_id",
        )
        float_fields = ("price_per_card", "price_bulk", "total_price")
        bool_fields = (
            "include_batch_number",
            "count_by_seconds",
            "count_from_first_connect",
            "switch_to_mac_on_connect",
            "lock_to_mac_on_close",
            "phone_only_login",
            "random_generation_enabled",
            "auto_renew_after_first_use",
            "transfer_to_student_status_on_connect",
            "close_user_session_on_disconnect",
            "allow_entry_by_previous_card_palestine",
        )

        for field in text_fields:
            if field in data:
                changes[field] = str(data.get(field) or "").strip()[:500]
        for field in int_fields:
            if field in data:
                changes[field] = self._int(data.get(field))
        for field in float_fields:
            if field in data:
                changes[field] = self._float(data.get(field))
        for field in bool_fields:
            if field in data:
                changes[field] = int(self._bool(data.get(field)))
        if "expire_at" in data:
            value = str(data.get("expire_at") or "").strip()
            changes["expire_at"] = value or None

        if "plan_id" in changes:
            if changes["plan_id"] <= 0:
                raise RadiusValidationError("الباقة المرتبطة مطلوبة")
            self._adapter.get_profile(changes["plan_id"])
        if "count" in changes:
            if changes["count"] < max(1, batch.generated):
                raise RadiusValidationError("عدد الدفعة لا يمكن أن يكون أقل من عدد الكروت المولدة")
            if changes["count"] > 2000:
                raise RadiusValidationError("عدد الدفعة يجب ألا يتجاوز 2000")
        if "username_length" in changes:
            changes["username_length"] = max(4, min(changes["username_length"], 32))
        if "password_length" in changes:
            changes["password_length"] = max(4, min(changes["password_length"], 64))
        if "device_count" in changes:
            changes["device_count"] = max(1, min(changes["device_count"], 50))

        updated = self._store.update_batch(batch_id, changes)
        if not updated:
            raise RadiusValidationError("تعذر تعديل دفعة الكروت")
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_UPDATE,
            target_type="card_batch",
            target_id=str(batch_id),
            payload={"changed_fields": sorted(changes.keys())},
        )
        return updated

    def revoke_card(self, *, actor: str, card_id: int) -> None:
        self._store.revoke(card_id)
        self._audit.record(actor=actor, action=AUDIT_ACTION_REVOKE,
                           target_type="card", target_id=str(card_id))

    def archive_batch(self, *, actor: str, batch_id: int, reason: str = "") -> bool:
        archived = cards_repo.archive_batch(
            self._store_tenant_id(), batch_id, actor=actor, reason=reason,
        )
        if archived:
            self._audit.record(
                actor=actor,
                action=AUDIT_ACTION_BATCH_ARCHIVE,
                target_type="card_batch",
                target_id=str(batch_id),
                payload=roadmap_audit_payload(
                    domain="card_batches",
                    action=AUDIT_ACTION_BATCH_ARCHIVE,
                    reason=reason,
                ),
            )
        return archived


def get_cards_service() -> CardsService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return CardsService(get_radius_adapter(), audit=get_audit_service())
