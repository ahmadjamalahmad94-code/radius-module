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
from ..core.system_config import default_currency
from ..core.types import Subscriber
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService


class UsersService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, status: Optional[str] = None, plan_id: Optional[int] = None,
             user_type: Optional[str] = "subscriber",
             search: str = "", expiring_within_days: Optional[int] = None,
             limit: int = 500, offset: int = 0) -> Sequence[Subscriber]:
        """قائمة المشتركين.

        R9.0:
          - `user_type='subscriber'` افتراضياً يستبعد سجلّات mirror التي
            يُنشئها card generation (user_type='card'). صفحة "المشتركين"
            تعرض المشتركين الحقيقيين فقط؛ البطاقات لها صفحة منفصلة.
            تمرير `user_type=None` صراحةً يُعيد السلوك القديم (الكل).
            تمرير `user_type='card'` يعرض البطاقات.
          - `search` يُمرَّر إلى SQL pushdown في الـ adapter/repo بدل
            الفلترة بعد LIMIT. مهم مع >1000 سجلّ.
          - `expiring_within_days`: حصْر النتائج على من تنتهي صلاحيتهم
            خلال N أيام (يطابق حساب «ينتهي قريبًا» في الـ Dashboard).
        """
        items = list(self._adapter.list_accounts(
            status=status, user_type=user_type, search=(search or None),
            expiring_within_days=expiring_within_days,
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
        _notify_alert(saved.tenant_id, "subscriber_new", {
            "full_name": saved.full_name or "—", "username": saved.username,
            "plan": _plan_label(saved.tenant_id, saved.plan_id),
            "mobile": saved.mobile or "—", "actor": actor,
        }, dedup_key=saved.username)
        return saved

    def update(self, *, actor: str, sub: Subscriber) -> Subscriber:
        # Fetch the current row UP-FRONT — it serves two purposes and is
        # non-fatal if it fails (brand-new subscriber / lookup error):
        #   1) password preservation (defense in depth, see below);
        #   2) computing the human-readable change diff for the
        #      «تعديل بيانات مشترك» alert (instead of a hardcoded «—»).
        try:
            existing = self._adapter.get_account(sub.username)
        except Exception:  # noqa: BLE001 — lookup failure must not break update
            existing = None

        # Defense in depth — protect the stored password from being
        # silently wiped by a form submit (or any caller) that didn't
        # carry the password field. RADIUS PAP/CHAP needs the cleartext
        # password; once erased the subscriber can never log in again,
        # and the only fix is asking the operator to remember/reset
        # the password. So: if the incoming DTO has an empty password
        # AND the subscriber already exists with a non-empty one,
        # preserve the existing value. The dedicated reset_password()
        # path is the ONLY way to clear/change a password.
        if not (sub.password or "").strip():
            if existing and (existing.password or "").strip():
                from dataclasses import replace
                sub = replace(sub, password=existing.password)
        _validate(sub)
        saved = self._adapter.upsert_account(sub)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="user", target_id=saved.username)
        _notify_alert(saved.tenant_id, "subscriber_edited", {
            "username": saved.username, "full_name": saved.full_name or "—",
            "changed": _describe_subscriber_changes(existing, saved),
            "actor": actor,
        }, dedup_key=saved.username)
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
                currency=(getattr(new_plan, "currency", "") or default_currency()),
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
        # إشعار المشترك بتغيير باقته (عبر المحرّك الموحّد؛ {prof} من السياق،
        # {old_prof} إضافيّ). يُسلَّم للقنوات المُفعَّلة في «إشعارات المشتركين».
        _notify_subscriber(saved.tenant_id, "plan_changed", subscriber=saved,
                           context={"old_prof": getattr(old_plan, "name", "") or ""})
        return {
            "subscriber": saved,
            "old_plan": old_plan,
            "new_plan": new_plan,
            "policy": policy,
            "remaining_minutes": remaining,
            "minute_delta": minute_delta,
            "debt_amount": debt_amount,
        }

    def send_sms(self, *, actor: str, username: str, message: str,
                 channel: str = "sms") -> dict:
        # القناة: «sms» أو «whatsapp» — كلاهما قنوات HTTP مفعّلة في محرك
        # الإشعارات (comms_providers.HTTP_CHANNELS)؛ أي قيمة أخرى مرفوضة.
        ch = (channel or "sms").strip().lower()
        if ch not in {"sms", "whatsapp"}:
            raise RadiusValidationError("unsupported message channel")
        body = (message or "").strip()
        if not body:
            raise RadiusValidationError("message required")
        sub = self._adapter.get_account(username)
        if not sub.id:
            raise RadiusValidationError("subscriber id required")
        if not (sub.mobile or "").strip():
            raise RadiusValidationError("subscriber mobile is empty")

        # تعويض {username} بالاسم الفعلي — مفيد في الإرسال الجماعي حيث
        # تُرسل نفس الرسالة لعدة مشتركين (الواجهة تُبقي المتغيّر كما هو).
        body = body.replace("{username}", username)

        from .notification_campaigns import NotificationCampaignError, NotificationCampaignService

        try:
            result = NotificationCampaignService(tenant_id=sub.tenant_id).send_manual(
                audience={"target": "selected_subscribers", "ids": [int(sub.id)], "limit": 1},
                channel=ch,
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
            payload={"queued_count": result.get("queued_count", 0), "channel": ch},
        )
        return result

    def reset_daily_quota(self, *, actor: str, username: str,
                          charge_mode: str = "free", amount: float = 0.0,
                          currency: str = "", notes: str = "") -> Subscriber:
        """Refresh the subscriber's daily allowance (zero the used counters).

        Optionally bills the restore like add_quota: free (no charge), paid
        (cash credited to the ledger), or debt (recorded as a debit + the
        amount subtracted from the subscriber balance). Defaults to free, so
        existing callers keep the original no-cost behaviour.
        """
        currency = currency or default_currency()
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("unknown reset charge mode")
        if charge_mode in {"paid", "debt"} and amount <= 0:
            raise RadiusValidationError("amount must be > 0")

        sub = self._adapter.get_account(username)
        changes = {
            "used_seconds": 0,
            "used_bytes_in": 0,
            "used_bytes_out": 0,
            "balance": float(sub.balance or 0),
        }
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
                source_type="subscriber_daily_quota_reset",
                notes=notes or ("استعادة كوتة يومية مدفوعة" if charge_mode == "paid"
                                else "استعادة كوتة يومية على الدين"),
                metadata={"charge_mode": charge_mode},
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
                "charge_mode": charge_mode,
                "amount": float(amount) if charge_mode in {"paid", "debt"} else 0,
            },
        )
        return saved

    def add_quota(self, *, actor: str, username: str, quota_mb: int,
                  quota_target: str = "combined", charge_mode: str = "free",
                  amount: float = 0.0, currency: str = "",
                  notes: str = "") -> Subscriber:
        currency = currency or default_currency()
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
                         currency: str = "", notes: str = "",
                         settled_deduction: float = 0.0) -> Subscriber:
        currency = currency or default_currency()
        if amount <= 0:
            raise RadiusValidationError("amount must be > 0")
        # Net wallet credit = cash received − the part used to settle open loans.
        # Loans the operator chose to «خصم» are cleared separately (their own
        # settlement ledger), so ONLY the remainder lands in the wallet — the
        # balance therefore reflects the deductions. With no settlements
        # (settled_deduction=0) this is a plain full-amount credit as before.
        settled_deduction = max(float(settled_deduction or 0.0), 0.0)
        credit = round(max(float(amount) - settled_deduction, 0.0), 2)
        sub = self._adapter.get_account(username)
        previous = float(sub.balance or 0)
        saved = self._adapter.upsert_account(
            replace(sub, balance=previous + credit)
        )
        if credit > 0:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="cash_balance",
                direction="credit",
                amount=credit,
                currency=currency,
                source_type="subscriber_cash_balance",
                notes=notes or "إضافة رصيد نقدي",
                metadata={
                    "previous_balance": previous,
                    "new_balance": float(saved.balance or 0),
                    "gross_amount": round(float(amount), 2),
                    "settled_deduction": round(settled_deduction, 2),
                },
            )
        self._audit.record(
            actor=actor,
            action="subscriber.cash_balance_add",
            target_type="user",
            target_id=username,
            payload={
                "amount": round(float(amount), 2),
                "credited": credit,
                "settled_deduction": round(settled_deduction, 2),
                "currency": currency,
            },
        )
        return saved

    def apply_payment_to_balance(self, *, actor: str, username: str,
                                 amount: float) -> float:
        """يسوي جزءًا من دفعة نقدية مع رصيد سالب مسجل كدين.

        يرفع الرصيد باتجاه الصفر دون تجاوزه، ويسجل قيد `debt_settlement`
        موازنًا لقيد الدين الأصلي. تقارير الدخل المبنية على `payment`
        تبقى كما هي. ترجع الدالة المبلغ الذي تم تطبيقه فعليًا.
        """
        if amount is None or float(amount) <= 0:
            return 0.0
        sub = self._adapter.get_account(username)
        previous = float(sub.balance or 0)
        due = max(-previous, 0.0)
        settle = round(min(float(amount), due), 2)
        if settle <= 0:
            return 0.0
        saved = self._adapter.upsert_account(replace(sub, balance=previous + settle))
        _record_subscriber_ledger(
            actor=actor,
            subscriber=saved,
            entry_type="debt_settlement",
            direction="credit",
            amount=settle,
            currency=default_currency(),
            source_type="payment_balance_settlement",
            notes="تسوية دين من دفعة نقدية",
            metadata={
                "previous_balance": previous,
                "new_balance": float(saved.balance or 0),
            },
        )
        self._audit.record(
            actor=actor,
            action="subscriber.debt_settled_from_payment",
            target_type="user",
            target_id=username,
            payload={"amount": settle},
        )
        return settle

    def disable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        self._adapter.upsert_account(replace(u, status=STATUS_DISABLED))
        self._audit.record(actor=actor, action=AUDIT_ACTION_DISABLE,
                           target_type="user", target_id=username)
        _notify_subscriber(u.tenant_id, "subscriber_disabled", subscriber=u)

    def enable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        self._adapter.upsert_account(replace(u, status=STATUS_ENABLED))
        self._audit.record(actor=actor, action=AUDIT_ACTION_ENABLE,
                           target_type="user", target_id=username)
        _notify_subscriber(u.tenant_id, "subscriber_reactivated", subscriber=u)

    def reset_password(self, *, actor: str, username: str, new_password: str) -> None:
        if not new_password:
            raise RadiusValidationError("new password required")
        self._adapter.reset_password(username, new_password)
        self._audit.record(actor=actor, action=AUDIT_ACTION_RESET_PASSWORD,
                           target_type="user", target_id=username)

    def extend_time(self, *, actor: str, username: str, minutes: int,
                    charge_mode: str = "free", amount: float = 0.0,
                    currency: str = "", notes: str = "") -> Subscriber:
        if minutes <= 0:
            raise RadiusValidationError("minutes > 0 required")
        currency = currency or default_currency()
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("unknown extend charge mode")
        if charge_mode in {"paid", "debt"} and amount <= 0:
            raise RadiusValidationError("amount must be > 0")
        u = self._adapter.get_account(username)
        new_exp = (u.expire_at or datetime.utcnow()) + timedelta(minutes=minutes)
        new_balance = float(u.balance or 0)
        if charge_mode == "debt":
            new_balance -= float(amount)
        saved = self._adapter.upsert_account(replace(u, expire_at=new_exp, balance=new_balance))
        if charge_mode in {"paid", "debt"}:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="time_extension" if charge_mode == "paid" else "debt",
                direction="credit" if charge_mode == "paid" else "debit",
                amount=float(amount),
                currency=currency,
                source_type="subscriber_time_extension",
                notes=notes or ("إضافة وقت مدفوعة" if charge_mode == "paid"
                                else "إضافة وقت على الدين"),
                metadata={"minutes": minutes, "charge_mode": charge_mode},
            )
        self._audit.record(actor=actor, action="extend_time",
                           target_type="user", target_id=username,
                           payload={"minutes": minutes, "new_expire_at": new_exp.isoformat(),
                                    "charge_mode": charge_mode,
                                    "amount": float(amount) if charge_mode in {"paid", "debt"} else 0})
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
            currency=(currency or default_currency()).upper()[:8],
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


# ── تنبيهات الإدارة (تلجرام) — محصّنة، لا تكسر العملية أبدًا ──────────────
def _notify_alert(tenant_id, key: str, context: dict, *, dedup_key: str = "") -> None:
    try:
        from .admin_alerts import dispatch
        dispatch(int(tenant_id or 1), key, context, dedup_key=dedup_key)
    except Exception:  # noqa: BLE001
        pass


def _notify_subscriber(tenant_id, event_key: str, *, subscriber=None,
                       context: dict | None = None) -> None:
    """يُسلّم إشعار حدث للمشترك عبر المحرّك الموحّد notifications_engine (المصدر
    الوحيد لإعدادات/تسليم إشعارات المشترك). محصّن — لا يكسر العملية أبدًا."""
    try:
        from .notifications_engine import notify_event
        notify_event(event_key, tenant_id=int(tenant_id or 1),
                     subscriber=subscriber, context=context or {})
    except Exception:  # noqa: BLE001
        pass


def _plan_label(tenant_id, plan_id) -> str:
    if not plan_id:
        return "—"
    try:
        from ..db.repos import plans_repo
        plan = plans_repo.get_plan(int(tenant_id or 1), int(plan_id))
        return (getattr(plan, "name", "") or "—") if plan else "—"
    except Exception:  # noqa: BLE001
        return "—"


# حالة المشترك بالعربيّة (للـ diff المقروء في تنبيه «تعديل بيانات مشترك»).
_STATUS_AR: dict[str, str] = {
    "enabled": "مفعّل", "disabled": "معطّل", "expired": "منتهٍ",
    "suspended": "موقوف", "pending": "بانتظار", "banned": "محظور",
    "active": "نشط",
}


def _describe_subscriber_changes(old, new) -> str:
    """يبني وصفًا عربيًّا مقروءًا لما تغيّر فعليًّا بين الحالة القديمة والجديدة
    للمشترك (يُغذّي حقل ``changed`` في تنبيه «تعديل بيانات مشترك»).

    لكلّ حقل ذي معنى نُدرج «القديم → الجديد» فقط إن اختلف، ونصِل بـ«، ». لو لم
    يتغيّر شيء جوهريّ نُرجِع «لا تغييرات جوهرية»؛ ولو تعذّر جلب الحالة القديمة
    (مشترك جديد/خطأ بحث) نُرجِع «—» دفاعيًّا — التنبيه لا يكسر التحديث أبدًا.

    ملاحظة: لا نُقارن الصلاحية (expire_at) هنا لأنّ نموذج التعديل لا يحملها في
    الـ DTO (تُدار عبر مسار التجديد/التمديد المنفصل)، فمقارنتها تُنتج ضجيجًا."""
    if old is None:
        return "—"
    try:
        tid = getattr(new, "tenant_id", None) or getattr(old, "tenant_id", 1)

        def _txt(s, attr):
            return (getattr(s, attr, "") or "").strip() or "—"

        def _plan(s):
            return _plan_label(tid, getattr(s, "plan_id", None))

        def _status(s):
            v = (getattr(s, "status", "") or "").strip()
            return _STATUS_AR.get(v, v) if v else "—"

        def _speed(s):
            d = int(getattr(s, "download_speed_kbps", 0) or 0)
            u = int(getattr(s, "upload_speed_kbps", 0) or 0)
            return f"{d}/{u} kbps" if (d or u) else "—"

        def _quota(s):
            mb = int(getattr(s, "combined_quota_mb", 0) or 0)
            return f"{mb} م.ب" if mb else "—"

        fields = [
            ("الاسم", _txt(old, "full_name"), _txt(new, "full_name")),
            ("الجوال", _txt(old, "mobile"), _txt(new, "mobile")),
            ("الباقة", _plan(old), _plan(new)),
            ("الحالة", _status(old), _status(new)),
            ("السرعة", _speed(old), _speed(new)),
            ("الكوتا", _quota(old), _quota(new)),
        ]
        parts = [f"{label}: {o} → {n}" for (label, o, n) in fields if o != n]

        # كلمة المرور — لا تُطبَع أبدًا، يُذكَر فقط أنها تغيّرت.
        op = getattr(old, "password", "") or ""
        npw = getattr(new, "password", "") or ""
        if npw and op != npw:
            parts.append("كلمة المرور: تم التغيير")

        return "، ".join(parts) if parts else "لا تغييرات جوهرية"
    except Exception:  # noqa: BLE001 — الوصف لا يكسر التحديث/التنبيه أبدًا
        return "—"
