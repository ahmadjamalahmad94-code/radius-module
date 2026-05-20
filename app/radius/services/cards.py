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

    def list_batch_operations(self, **kw) -> list[dict]:
        return self._store.list_batch_operations(**kw)

    def count_batch_operations(self, **kw) -> int:
        return self._store.count_batch_operations(**kw)

    def batch_operations_totals(self, **kw) -> dict:
        return self._store.batch_operations_totals(**kw)

    def list_cards(self, **kw):
        return self._store.list_cards(**kw)

    def count_cards(self, **kw) -> int:
        """R10.4: عدّ الكروت — للـ pagination في cards_list."""
        return self._store.count_cards(**kw)

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

    def enable_card(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.set_card_revoked(self._store_tenant_id(), card_id, False, actor=actor):
            raise RadiusValidationError("تعذر تفعيل البطاقة")
        self._audit.record(actor=actor, action="card.enable",
                           target_type="card", target_id=str(card_id))

    def disable_card(self, *, actor: str, card_id: int, reason: str = "") -> None:
        if not cards_repo.set_card_revoked(
            self._store_tenant_id(), card_id, True, actor=actor, reason=reason,
        ):
            raise RadiusValidationError("تعذر تعطيل البطاقة")
        self._audit.record(actor=actor, action="card.disable",
                           target_type="card", target_id=str(card_id),
                           payload={"reason": reason})

    def lock_card_mac(self, *, actor: str, card_id: int, mac: str) -> None:
        mac = (mac or "").strip()
        if not mac:
            raise RadiusValidationError("MAC مطلوب")
        if not cards_repo.set_card_locked_mac(self._store_tenant_id(), card_id, mac, actor=actor):
            raise RadiusValidationError("تعذر تثبيت MAC")
        self._audit.record(actor=actor, action="card.lock_mac",
                           target_type="card", target_id=str(card_id),
                           payload={"mac": mac})

    def unlock_card_mac(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.set_card_locked_mac(self._store_tenant_id(), card_id, "", actor=actor):
            raise RadiusValidationError("تعذر إلغاء تثبيت MAC")
        self._audit.record(actor=actor, action="card.unlock_mac",
                           target_type="card", target_id=str(card_id))

    def reset_card_usage(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.reset_card_usage(self._store_tenant_id(), card_id):
            raise RadiusValidationError("تعذر تصفير استخدام البطاقة")
        self._audit.record(actor=actor, action="card.reset_usage",
                           target_type="card", target_id=str(card_id))

    def set_card_speed(self, *, actor: str, card_id: int,
                         down_kbps: int, up_kbps: int,
                         username: str = "") -> dict:
        """Persist a per-card Mikrotik-Rate-Limit override, write it
        through to FreeRADIUS (radreply), and best-effort CoA-push it
        to any live session for `username`.

        Pass down_kbps=0 AND up_kbps=0 to CLEAR the override (falls back
        to the plan default). Mixing 0 + nonzero is rejected because we
        always emit a "up/down k" pair to MT.

        Returns a dict with keys: {down, up, was_override, fr_synced,
        coa_result}. Raises RadiusValidationError on bad input or missing
        card.
        """
        down = int(down_kbps or 0)
        up   = int(up_kbps   or 0)
        if down < 0 or up < 0:
            raise RadiusValidationError("لا تُقبل قيم سالبة للسرعة")
        clearing = (down == 0 and up == 0)
        if not clearing and (down == 0 or up == 0):
            raise RadiusValidationError(
                "يجب تحديد قيمتي التنزيل والرفع معًا (أو تصفير الاثنين لإلغاء التخصيص)."
            )
        tenant_id = self._store_tenant_id()

        # ── 1) DB persist ──
        result = cards_repo.set_card_speed_override(tenant_id, card_id, down, up)
        if result is None:
            raise RadiusValidationError("لم يتم العثور على البطاقة")
        username = (username or result.get("username") or "").strip()

        # ── 2) FreeRADIUS native path: re-sync radreply for this card's
        #    subscriber-mirror so the new override appears in DB rows that
        #    rlm_sql reads at next Access-Request. Best-effort — if no
        #    subscriber mirror exists, the HTTP /api/internal/auth path
        #    (policy_engine._card_to_subscriber) still picks the override
        #    from the cards row directly.
        fr_synced = False
        try:
            from ..db.repos import subscribers_repo, plans_repo
            from . import freeradius_translator
            sub = subscribers_repo.get_subscriber(tenant_id, username) if username else None
            if sub is not None:
                # Stamp the override onto the Subscriber DTO so sync_subscriber
                # writes the same Mikrotik-Rate-Limit row we expect from the
                # policy_engine path. Both paths end up with the same value.
                from dataclasses import replace
                stamped = replace(
                    sub,
                    bandwidth_control_enabled=(not clearing),
                    download_speed_kbps=(down if not clearing else 0),
                    upload_speed_kbps=(up   if not clearing else 0),
                )
                plan = plans_repo.get_plan(tenant_id, sub.plan_id) if sub.plan_id else None
                freeradius_translator.sync_subscriber(stamped, plan)
                fr_synced = True
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "freeradius_translator re-sync failed for card %s: %s", username, e
            )

        # ── 3) Best-effort CoA push so live MT session picks the new
        #    Mikrotik-Rate-Limit without waiting for re-auth. Same shape
        #    as adjust_card_time but a different attribute.
        coa_result = None
        try:
            push_coa = getattr(self._adapter, "push_rate_limit", None)
            if callable(push_coa) and username:
                rate = (f"{up}k/{down}k" if not clearing else "")
                coa_result = push_coa(username=username, new_rate_limit=rate)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "CoA push_rate_limit failed for %s: %s", username, e
            )

        # ── 4) Audit ──
        self._audit.record(
            actor=actor, action="card.set_speed",
            target_type="card", target_id=str(card_id),
            payload={
                "down_kbps":    down,
                "up_kbps":      up,
                "cleared":      clearing,
                "was_override": result["was_override"],
                "fr_synced":    fr_synced,
                "coa_pushed":   bool(coa_result and getattr(coa_result, "ok", False)),
            },
        )
        return {
            **result,
            "fr_synced":  fr_synced,
            "coa_result": coa_result,
        }

    def adjust_card_time(self, *, actor: str, card_id: int,
                          delta_seconds: int, username: str = "") -> dict:
        """Shift the card's expire_at by +/- delta_seconds and (best-effort)
        push a CoA Session-Timeout update to any live MikroTik session so
        the change takes effect without disconnecting the user.

        Returns the repo result dict; raises RadiusValidationError for any
        precondition failure (zero delta / card not found / not activated).

        CoA push is best-effort — if it fails we still keep the DB write,
        log a warning, and return so the caller can flash a helpful note.
        """
        if delta_seconds == 0:
            raise RadiusValidationError("لا يوجد تعديل لتطبيقه")
        tenant_id = self._store_tenant_id()
        result = cards_repo.adjust_card_expire_at(tenant_id, card_id, delta_seconds)
        if result is None:
            raise RadiusValidationError(
                "تعذر تعديل وقت البطاقة — تأكد أنها مفعّلة (لها وقت انتهاء)."
            )

        # ── Best-effort CoA push to update Session-Timeout on live sessions
        # We delegate to the adapter so each adapter implementation can decide
        # how to enumerate active NAS endpoints (MikroTik vs ManualAdapter).
        coa_result = None
        try:
            push_coa = getattr(self._adapter, "push_session_timeout", None)
            if callable(push_coa) and username:
                coa_result = push_coa(
                    username=username,
                    session_timeout=result["remaining_seconds"],
                )
        except Exception as e:  # noqa: BLE001 — never let CoA failure mask the DB write
            import logging
            logging.getLogger(__name__).warning(
                "CoA push_session_timeout failed for %s: %s", username, e
            )
            coa_result = None

        # ── Audit
        self._audit.record(
            actor=actor, action="card.adjust_time",
            target_type="card", target_id=str(card_id),
            payload={
                "delta_seconds":      delta_seconds,
                "expire_at_old":      result["expire_at_old"],
                "expire_at_new":      result["expire_at_new"],
                "remaining_seconds":  result["remaining_seconds"],
                "coa_pushed":         bool(coa_result and getattr(coa_result, "ok", False)),
            },
        )
        return {
            **result,
            "coa_result": coa_result,
        }

    def delete_card_permanently(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.delete_card_permanently(self._store_tenant_id(), card_id):
            raise RadiusValidationError("تعذر حذف البطاقة")
        self._audit.record(actor=actor, action="card.delete_permanent",
                           target_type="card", target_id=str(card_id))

    def disconnect_card(self, *, actor: str, username: str, session_id: str = "") -> None:
        self._adapter.disconnect(username, session_id=session_id or None)
        self._audit.record(actor=actor, action="card.disconnect",
                           target_type="card", target_id=username,
                           payload={"session_id": session_id or ""})

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

    def restore_batch(self, *, actor: str, batch_id: int) -> bool:
        restored = cards_repo.restore_batch(self._store_tenant_id(), batch_id, actor=actor)
        if restored:
            self._audit.record(
                actor=actor,
                action="card_batch.restore",
                target_type="card_batch",
                target_id=str(batch_id),
                payload=roadmap_audit_payload(
                    domain="card_batches",
                    action="card_batch.restore",
                ),
            )
        return restored


def get_cards_service() -> CardsService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return CardsService(get_radius_adapter(), audit=get_audit_service())
