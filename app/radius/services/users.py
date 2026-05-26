"""UsersService — المشتركون (Radius Accounts)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional, Sequence

from ..core.constants import (
    AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE, AUDIT_ACTION_DISABLE,
    AUDIT_ACTION_ENABLE, AUDIT_ACTION_RESET_PASSWORD, AUDIT_ACTION_UPDATE,
    STATUS_DISABLED, STATUS_ENABLED, USER_TYPES,
)
from ..core.errors import RadiusValidationError
from ..core.types import Subscriber
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService


class UsersService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, status: Optional[str] = None, plan_id: Optional[int] = None,
             user_type: Optional[str] = "subscriber",
             search: str = "", limit: int = 500, offset: int = 0) -> Sequence[Subscriber]:
        """قائمة المشتركين.

        R9.0:
          - `user_type='subscriber'` افتراضياً يستبعد سجلّات mirror التي
            يُنشئها card generation (user_type='card'). صفحة "المشتركين"
            تعرض المشتركين الحقيقيين فقط؛ البطاقات لها صفحة منفصلة.
            تمرير `user_type=None` صراحةً يُعيد السلوك القديم (الكل).
            تمرير `user_type='card'` يعرض البطاقات.
          - `search` يُمرَّر إلى SQL pushdown في الـ adapter/repo بدل
            الفلترة بعد LIMIT. مهم مع >1000 سجلّ.
        """
        items = list(self._adapter.list_accounts(
            status=status, user_type=user_type, search=(search or None),
            limit=limit, offset=offset,
        ))
        if plan_id is not None:
            items = [u for u in items if u.plan_id == plan_id]
        return items

    def get(self, username: str) -> Subscriber:
        return self._adapter.get_account(username)

    def create(self, *, actor: str, sub: Subscriber) -> Subscriber:
        _validate(sub)
        saved = self._adapter.upsert_account(sub)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="user", target_id=saved.username,
                           payload={"plan_id": saved.plan_id})
        return saved

    def update(self, *, actor: str, sub: Subscriber) -> Subscriber:
        _validate(sub)
        saved = self._adapter.upsert_account(sub)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="user", target_id=saved.username)
        return saved

    def change_plan(self, *, actor: str, username: str, plan_id: int,
                    policy: str) -> dict:
        if plan_id <= 0:
            raise RadiusValidationError("plan_id required")
        allowed = {
            "lower_compensate",
            "lower_keep_expiry",
            "higher_debt",
            "higher_reduce_days",
            "higher_keep_expiry",
            "neutral_keep_expiry",
        }
        if policy not in allowed:
            raise RadiusValidationError("unknown plan change policy")

        sub = self._adapter.get_account(username)
        old_plan = None
        if sub.plan_id:
            try:
                old_plan = self._adapter.get_profile(int(sub.plan_id))
            except Exception:  # noqa: BLE001
                old_plan = None
        new_plan = self._adapter.get_profile(plan_id)

        old_price = float(getattr(old_plan, "price", 0) or 0)
        new_price = float(getattr(new_plan, "price", 0) or 0)
        if policy.startswith("lower_") and old_price and new_price >= old_price:
            raise RadiusValidationError("selected plan is not cheaper")
        if policy.startswith("higher_") and old_price and new_price <= old_price:
            raise RadiusValidationError("selected plan is not more expensive")

        now = datetime.utcnow()
        remaining = _remaining_minutes(sub.expire_at, now)
        new_expire_at = sub.expire_at
        minute_delta = 0
        debt_amount = 0.0

        if policy in {"lower_compensate", "higher_reduce_days", "higher_debt"}:
            old_rate = _minute_rate(old_plan)
            new_rate = _minute_rate(new_plan)
            if remaining > 0 and (old_rate <= 0 or new_rate <= 0):
                raise RadiusValidationError("plan price and duration are required for this option")
            if policy == "lower_compensate" and remaining > 0:
                adjusted = max(remaining, int(round((remaining * old_rate) / new_rate)))
                new_expire_at = now + timedelta(minutes=adjusted)
                minute_delta = adjusted - remaining
            elif policy == "higher_reduce_days" and remaining > 0:
                adjusted = min(remaining, int(round((remaining * old_rate) / new_rate)))
                new_expire_at = now + timedelta(minutes=max(0, adjusted))
                minute_delta = adjusted - remaining
            elif policy == "higher_debt" and remaining > 0:
                debt_amount = round(max((new_rate - old_rate) * remaining, 0), 2)

        new_balance = float(sub.balance or 0) - debt_amount
        saved = self._adapter.upsert_account(
            replace(
                sub,
                plan_id=plan_id,
                expire_at=new_expire_at,
                balance=new_balance,
            )
        )
        if debt_amount > 0:
            _record_plan_change_debt(
                actor=actor,
                subscriber=saved,
                old_plan_id=sub.plan_id,
                new_plan_id=plan_id,
                amount=debt_amount,
                currency=(getattr(new_plan, "currency", "") or "JOD"),
                remaining_minutes=remaining,
            )
        self._audit.record(
            actor=actor,
            action="change_plan",
            target_type="user",
            target_id=username,
            payload={
                "old_plan_id": sub.plan_id,
                "new_plan_id": plan_id,
                "policy": policy,
                "old_price": old_price,
                "new_price": new_price,
                "remaining_minutes": remaining,
                "minute_delta": minute_delta,
                "debt_amount": debt_amount,
                "new_expire_at": new_expire_at.isoformat() if new_expire_at else None,
            },
        )
        return {
            "subscriber": saved,
            "old_plan": old_plan,
            "new_plan": new_plan,
            "policy": policy,
            "remaining_minutes": remaining,
            "minute_delta": minute_delta,
            "debt_amount": debt_amount,
        }

    def send_sms(self, *, actor: str, username: str, message: str) -> dict:
        body = (message or "").strip()
        if not body:
            raise RadiusValidationError("message required")
        sub = self._adapter.get_account(username)
        if not sub.id:
            raise RadiusValidationError("subscriber id required")
        if not (sub.mobile or "").strip():
            raise RadiusValidationError("subscriber mobile is empty")

        from .notification_campaigns import NotificationCampaignError, NotificationCampaignService

        try:
            result = NotificationCampaignService(tenant_id=sub.tenant_id).send_manual(
                audience={"target": "selected_subscribers", "ids": [int(sub.id)], "limit": 1},
                channel="sms",
                message=body,
                actor=actor,
            )
        except NotificationCampaignError as exc:
            raise RadiusValidationError(str(exc)) from exc
        self._audit.record(
            actor=actor,
            action="subscriber.sms_queue",
            target_type="user",
            target_id=username,
            payload={"queued_count": result.get("queued_count", 0)},
        )
        return result

    def reset_daily_quota(self, *, actor: str, username: str) -> Subscriber:
        sub = self._adapter.get_account(username)
        saved = self._adapter.upsert_account(
            replace(sub, used_seconds=0, used_bytes_in=0, used_bytes_out=0)
        )
        self._audit.record(
            actor=actor,
            action="subscriber.daily_quota_reset",
            target_type="user",
            target_id=username,
            payload={
                "previous_used_seconds": sub.used_seconds,
                "previous_used_bytes_in": sub.used_bytes_in,
                "previous_used_bytes_out": sub.used_bytes_out,
            },
        )
        return saved

    def add_quota(self, *, actor: str, username: str, quota_mb: int,
                  quota_target: str = "combined", charge_mode: str = "free",
                  amount: float = 0.0, currency: str = "JOD",
                  notes: str = "") -> Subscriber:
        if quota_mb <= 0:
            raise RadiusValidationError("quota_mb must be > 0")
        if quota_target not in {"combined", "download", "upload"}:
            raise RadiusValidationError("unknown quota target")
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("unknown quota charge mode")
        if charge_mode in {"paid", "debt"} and amount <= 0:
            raise RadiusValidationError("amount must be > 0")

        sub = self._adapter.get_account(username)
        changes = {
            "quota_limit_enabled": True,
            "combined_quota_mb": sub.combined_quota_mb,
            "download_quota_mb": sub.download_quota_mb,
            "upload_quota_mb": sub.upload_quota_mb,
            "balance": float(sub.balance or 0),
        }
        if quota_target == "combined":
            changes["combined_quota_mb"] = int(sub.combined_quota_mb or 0) + quota_mb
        elif quota_target == "download":
            changes["download_quota_mb"] = int(sub.download_quota_mb or 0) + quota_mb
        else:
            changes["upload_quota_mb"] = int(sub.upload_quota_mb or 0) + quota_mb
        if charge_mode == "debt":
            changes["balance"] = float(sub.balance or 0) - float(amount)
        saved = self._adapter.upsert_account(replace(sub, **changes))
        if charge_mode in {"paid", "debt"}:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="quota_topup" if charge_mode == "paid" else "debt",
                direction="credit" if charge_mode == "paid" else "debit",
                amount=float(amount),
                currency=currency,
                source_type="subscriber_quota_topup",
                notes=notes or ("إضافة كوتة مدفوعة" if charge_mode == "paid" else "إضافة كوتة على الدين"),
                metadata={
                    "quota_mb": quota_mb,
                    "quota_target": quota_target,
                    "charge_mode": charge_mode,
                },
            )
        self._audit.record(
            actor=actor,
            action="subscriber.quota_topup",
            target_type="user",
            target_id=username,
            payload={
                "quota_mb": quota_mb,
                "quota_target": quota_target,
                "charge_mode": charge_mode,
                "amount": amount,
                "currency": currency,
            },
        )
        return saved

    def add_cash_balance(self, *, actor: str, username: str, amount: float,
                         currency: str = "JOD", notes: str = "") -> Subscriber:
        if amount <= 0:
            raise RadiusValidationError("amount must be > 0")
        sub = self._adapter.get_account(username)
        saved = self._adapter.upsert_account(
            replace(sub, balance=float(sub.balance or 0) + float(amount))
        )
        _record_subscriber_ledger(
            actor=actor,
            subscriber=saved,
            entry_type="cash_balance",
            direction="credit",
            amount=float(amount),
            currency=currency,
            source_type="subscriber_cash_balance",
            notes=notes or "إضافة رصيد نقدي",
            metadata={
                "previous_balance": float(sub.balance or 0),
                "new_balance": float(saved.balance or 0),
            },
        )
        self._audit.record(
            actor=actor,
            action="subscriber.cash_balance_add",
            target_type="user",
            target_id=username,
            payload={"amount": amount, "currency": currency},
        )
        return saved

    def disable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        self._adapter.upsert_account(replace(u, status=STATUS_DISABLED))
        self._audit.record(actor=actor, action=AUDIT_ACTION_DISABLE,
                           target_type="user", target_id=username)

    def enable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        self._adapter.upsert_account(replace(u, status=STATUS_ENABLED))
        self._audit.record(actor=actor, action=AUDIT_ACTION_ENABLE,
                           target_type="user", target_id=username)

    def reset_password(self, *, actor: str, username: str, new_password: str) -> None:
        if not new_password:
            raise RadiusValidationError("new password required")
        self._adapter.reset_password(username, new_password)
        self._audit.record(actor=actor, action=AUDIT_ACTION_RESET_PASSWORD,
                           target_type="user", target_id=username)

    def extend_time(self, *, actor: str, username: str, minutes: int) -> Subscriber:
        if minutes <= 0:
            raise RadiusValidationError("minutes > 0 required")
        u = self._adapter.get_account(username)
        new_exp = (u.expire_at or datetime.utcnow()) + timedelta(minutes=minutes)
        saved = self._adapter.upsert_account(replace(u, expire_at=new_exp))
        self._audit.record(actor=actor, action="extend_time",
                           target_type="user", target_id=username,
                           payload={"minutes": minutes, "new_expire_at": new_exp.isoformat()})
        return saved

    def delete(self, *, actor: str, username: str) -> None:
        self._adapter.delete_account(username)
        self._audit.record(actor=actor, action=AUDIT_ACTION_ARCHIVE,
                           target_type="user", target_id=username,
                           payload={"mode": "soft_delete"})


def _validate(sub: Subscriber) -> None:
    if sub.user_type not in USER_TYPES:
        raise RadiusValidationError(f"unknown user_type: {sub.user_type!r}")
    if not sub.username:
        raise RadiusValidationError("username required")


def _plan_minutes(plan) -> int:
    if not plan:
        return 0
    if int(getattr(plan, "duration_minutes", 0) or 0) > 0:
        return int(plan.duration_minutes)
    if int(getattr(plan, "validity_days", 0) or 0) > 0:
        return int(plan.validity_days) * 24 * 60
    value = int(getattr(plan, "duration_value", 0) or 0)
    unit = str(getattr(plan, "duration_unit", "") or "").lower()
    if value <= 0:
        return 0
    if unit in {"mins", "min", "minute", "minutes"}:
        return value
    if unit in {"hrs", "hr", "hour", "hours"}:
        return value * 60
    if unit in {"days", "day"}:
        return value * 24 * 60
    if unit in {"months", "month"}:
        return value * 30 * 24 * 60
    return 0


def _minute_rate(plan) -> float:
    minutes = _plan_minutes(plan)
    price = float(getattr(plan, "price", 0) or 0)
    if minutes <= 0 or price <= 0:
        return 0.0
    return price / minutes


def _remaining_minutes(expire_at, now: datetime) -> int:
    if not expire_at:
        return 0
    return max(0, int((expire_at - now).total_seconds() // 60))


def _record_plan_change_debt(*, actor: str, subscriber: Subscriber,
                             old_plan_id: int | None, new_plan_id: int,
                             amount: float, currency: str,
                             remaining_minutes: int) -> None:
    _record_subscriber_ledger(
        actor=actor,
        subscriber=subscriber,
        entry_type="debt",
        amount=amount,
        direction="debit",
        currency=currency,
        source_type="subscriber_plan_change",
        notes="دين فرق تغيير العرض",
        metadata={
            "old_plan_id": old_plan_id,
            "new_plan_id": new_plan_id,
            "remaining_minutes": remaining_minutes,
        },
    )


def _record_subscriber_ledger(*, actor: str, subscriber: Subscriber,
                              entry_type: str, amount: float,
                              direction: str, currency: str,
                              source_type: str, notes: str,
                              metadata: dict) -> None:
    from ..db.connection import transaction
    from ..db.repos import accounting_repo

    with transaction() as conn:
        accounting_repo.create_ledger_entry(
            conn,
            tenant_id=subscriber.tenant_id,
            entry_type=entry_type,
            amount=amount,
            direction=direction,
            currency=(currency or "JOD").upper()[:8],
            subscriber_id=subscriber.id,
            username=subscriber.username,
            operator=actor,
            source_type=source_type,
            notes=notes,
            metadata=metadata,
        )


def get_users_service() -> UsersService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return UsersService(get_radius_adapter(), audit=get_audit_service())
