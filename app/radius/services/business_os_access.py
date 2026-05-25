"""Business OS access, scope, audit, and safety gate foundations.

This module is intentionally additive. It does not retrofit existing RADIUS
routes; it gives upcoming Business OS features a shared vocabulary for
permissions, ownership scope, limits, audit recording, and preflight gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .business_os_finance import EventService


PERM_FINANCE_VIEW = "finance.view"
PERM_FINANCE_WRITE = "finance.write"
PERM_WALLET_CREDIT = "wallet.credit"
PERM_WALLET_DEBIT = "wallet.debit"
PERM_LEDGER_VIEW = "ledger.view"
PERM_LEDGER_CORRECT = "ledger.correct"
PERM_SUBSCRIBERS_VIEW = "subscribers.view"
PERM_SUBSCRIBERS_WRITE = "subscribers.write"
PERM_CARD_USERS_VIEW = "card_users.view"
PERM_CARD_USERS_WRITE = "card_users.write"
PERM_CARDS_VIEW = "cards.view"
PERM_CARDS_WRITE = "cards.write"
PERM_MANAGERS_VIEW = "managers.view"
PERM_MANAGERS_WRITE = "managers.write"
PERM_DISTRIBUTORS_VIEW = "distributors.view"
PERM_DISTRIBUTORS_WRITE = "distributors.write"
PERM_NOTIFICATIONS_SEND = "notifications.send"
PERM_CAMPAIGNS_SEND = "campaigns.send"
PERM_EVENTS_VIEW = "events.view"
PERM_REPORTS_VIEW = "reports.view"
PERM_SPEED_CONTROL_WRITE = "speed_control.write"
PERM_APPROVALS_MANAGE = "approvals.manage"

BUSINESS_OS_PERMISSIONS: tuple[str, ...] = (
    PERM_FINANCE_VIEW,
    PERM_FINANCE_WRITE,
    PERM_WALLET_CREDIT,
    PERM_WALLET_DEBIT,
    PERM_LEDGER_VIEW,
    PERM_LEDGER_CORRECT,
    PERM_SUBSCRIBERS_VIEW,
    PERM_SUBSCRIBERS_WRITE,
    PERM_CARD_USERS_VIEW,
    PERM_CARD_USERS_WRITE,
    PERM_CARDS_VIEW,
    PERM_CARDS_WRITE,
    PERM_MANAGERS_VIEW,
    PERM_MANAGERS_WRITE,
    PERM_DISTRIBUTORS_VIEW,
    PERM_DISTRIBUTORS_WRITE,
    PERM_NOTIFICATIONS_SEND,
    PERM_CAMPAIGNS_SEND,
    PERM_EVENTS_VIEW,
    PERM_REPORTS_VIEW,
    PERM_SPEED_CONTROL_WRITE,
    PERM_APPROVALS_MANAGE,
)

SCOPE_COMPANY = "company"
SCOPE_BRANCH = "branch"
SCOPE_MANAGER = "manager"
SCOPE_DISTRIBUTOR = "distributor"
SCOPE_SUBSCRIBER = "subscriber"
SCOPE_CARD_USER = "card_user"

BUSINESS_OS_SCOPE_TYPES: tuple[str, ...] = (
    SCOPE_COMPANY,
    SCOPE_BRANCH,
    SCOPE_MANAGER,
    SCOPE_DISTRIBUTOR,
    SCOPE_SUBSCRIBER,
    SCOPE_CARD_USER,
)

ACTION_REQUIRED_PERMISSIONS: dict[str, str] = {
    "finance.view": PERM_FINANCE_VIEW,
    "finance.write": PERM_FINANCE_WRITE,
    "wallet.credit": PERM_WALLET_CREDIT,
    "wallet.debit": PERM_WALLET_DEBIT,
    "ledger.view": PERM_LEDGER_VIEW,
    "ledger.correct": PERM_LEDGER_CORRECT,
    "subscribers.view": PERM_SUBSCRIBERS_VIEW,
    "subscribers.write": PERM_SUBSCRIBERS_WRITE,
    "card_users.view": PERM_CARD_USERS_VIEW,
    "card_users.write": PERM_CARD_USERS_WRITE,
    "cards.view": PERM_CARDS_VIEW,
    "cards.write": PERM_CARDS_WRITE,
    "managers.view": PERM_MANAGERS_VIEW,
    "managers.write": PERM_MANAGERS_WRITE,
    "distributors.view": PERM_DISTRIBUTORS_VIEW,
    "distributors.write": PERM_DISTRIBUTORS_WRITE,
    "notifications.send": PERM_NOTIFICATIONS_SEND,
    "campaigns.send": PERM_CAMPAIGNS_SEND,
    "events.view": PERM_EVENTS_VIEW,
    "reports.view": PERM_REPORTS_VIEW,
    "speed_control.write": PERM_SPEED_CONTROL_WRITE,
    "approvals.manage": PERM_APPROVALS_MANAGE,
}


@dataclass(frozen=True)
class ScopeGrant:
    scope_type: str
    scope_id: int | str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"scope_type": self.scope_type, "scope_id": self.scope_id}


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    required_permission: str = ""
    missing_permission: str = ""
    requires_approval: bool = False
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "required_permission": self.required_permission,
            "missing_permission": self.missing_permission,
            "requires_approval": self.requires_approval,
            "violations": list(self.violations),
            "warnings": list(self.warnings),
        }


def _amount(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("amount must be numeric") from exc


class ScopeResolver:
    """Resolve allowed owner scopes from a lightweight actor contract."""

    def resolve(self, actor: dict[str, Any]) -> dict[str, Any]:
        actor_type = str(actor.get("actor_type") or actor.get("type") or "").strip()
        permissions = tuple(dict.fromkeys(str(p) for p in actor.get("permissions") or ()))
        if actor.get("is_super_admin") or actor_type in {"super_admin", "company", "admin"}:
            scopes = (ScopeGrant(SCOPE_COMPANY),)
        elif actor_type == "branch":
            scopes = (ScopeGrant(SCOPE_BRANCH, actor.get("branch_id") or actor.get("id")),)
        elif actor_type == "manager":
            scopes = (ScopeGrant(SCOPE_MANAGER, actor.get("manager_id") or actor.get("id")),)
        elif actor_type == "distributor":
            scopes = (ScopeGrant(SCOPE_DISTRIBUTOR, actor.get("distributor_id") or actor.get("id")),)
        elif actor_type == "subscriber":
            scopes = (ScopeGrant(SCOPE_SUBSCRIBER, actor.get("subscriber_id") or actor.get("id")),)
        elif actor_type == "card_user":
            scopes = (ScopeGrant(SCOPE_CARD_USER, actor.get("card_user_id") or actor.get("id")),)
        else:
            scopes = ()
        return {
            "actor_type": actor_type or "unknown",
            "permissions": list(permissions),
            "scopes": [scope.as_dict() for scope in scopes],
            "global_access": any(scope.scope_type == SCOPE_COMPANY for scope in scopes),
        }

    def can_access_owner(self, actor: dict[str, Any], owner_type: str, owner_id: Any = None) -> bool:
        resolved = self.resolve(actor)
        if resolved["global_access"]:
            return True
        for scope in resolved["scopes"]:
            if scope["scope_type"] == owner_type and str(scope["scope_id"]) == str(owner_id):
                return True
        return False


class LimitPolicy:
    """Central limit defaults for sensitive Business OS operations."""

    DEFAULTS: dict[str, Any] = {
        "max_free_days": 3,
        "max_loan_count": 2,
        "max_discount_amount": "50.00",
        "max_batch_creation_amount": "10000.00",
        "max_wallet_debit": "5000.00",
        "require_approval_threshold": "1000.00",
    }

    ACTION_LIMIT_KEY: dict[str, str] = {
        "wallet.debit": "max_wallet_debit",
        "discount.apply": "max_discount_amount",
        "cards.batch_create": "max_batch_creation_amount",
        "subscriber.free_days": "max_free_days",
        "loan.grant": "max_loan_count",
    }

    def evaluate(
        self,
        action: str,
        *,
        amount: Any = None,
        count: int | None = None,
        days: int | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = {**self.DEFAULTS, **(policy or {})}
        limit_key = self.ACTION_LIMIT_KEY.get(action, "")
        violations: list[str] = []
        warnings: list[str] = []
        requires_approval = False

        if limit_key in {"max_wallet_debit", "max_discount_amount", "max_batch_creation_amount"}:
            requested = _amount(amount)
            maximum = _amount(config[limit_key])
            approval_threshold = _amount(config["require_approval_threshold"])
            if requested > maximum:
                violations.append(f"{limit_key}_exceeded")
            if requested > approval_threshold:
                requires_approval = True
                warnings.append("approval_required")
        elif limit_key == "max_free_days" and days is not None:
            if int(days) > int(config[limit_key]):
                violations.append("max_free_days_exceeded")
        elif limit_key == "max_loan_count" and count is not None:
            if int(count) > int(config[limit_key]):
                violations.append("max_loan_count_exceeded")

        return {
            "action": action,
            "allowed": not violations,
            "limit_key": limit_key,
            "requires_approval": requires_approval,
            "violations": violations,
            "warnings": warnings,
        }


class SafetyGateService:
    """Composable permission and limit checks for future route enforcement."""

    def __init__(self, *, limit_policy: LimitPolicy | None = None) -> None:
        self.limit_policy = limit_policy or LimitPolicy()

    def check(
        self,
        action: str,
        *,
        permissions: tuple[str, ...] | list[str] = (),
        amount: Any = None,
        count: int | None = None,
        days: int | None = None,
        policy: dict[str, Any] | None = None,
    ) -> AccessDecision:
        held = set(permissions or ())
        required = ACTION_REQUIRED_PERMISSIONS.get(action, "")
        if required and required not in held and "admin:full" not in held and "*" not in held:
            return AccessDecision(False, required_permission=required, missing_permission=required)
        limit = self.limit_policy.evaluate(
            action,
            amount=amount,
            count=count,
            days=days,
            policy=policy,
        )
        return AccessDecision(
            bool(limit["allowed"]),
            required_permission=required,
            requires_approval=bool(limit["requires_approval"]),
            violations=tuple(limit["violations"]),
            warnings=tuple(limit["warnings"]),
        )


class AuditGuard:
    """Record a sensitive Business OS event plus best-effort legacy audit."""

    def __init__(self, *, event_service: EventService | None = None, audit_service: Any = None) -> None:
        self.event_service = event_service or EventService()
        self.audit_service = audit_service

    def record(
        self,
        *,
        tenant_id: int,
        actor_type: str,
        actor_id: int | None,
        action: str,
        target_type: str,
        target_id: int | str | None,
        reason: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "reason": reason,
            "before": before or {},
            "after": after or {},
            **(metadata or {}),
        }
        event = self.event_service.record_event(
            tenant_id=tenant_id,
            category="system",
            severity="info",
            event_key=f"business_os.{action}",
            message=f"{action} {target_type}",
            actor_type=actor_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            metadata=payload,
        )
        if self.audit_service is not None:
            try:
                self.audit_service.record(
                    actor=f"{actor_type}:{actor_id or 'unknown'}",
                    action=action,
                    target_type=target_type,
                    target_id=str(target_id or ""),
                    payload=payload,
                    before=before or {},
                    after=after or {},
                )
            except Exception:
                pass
        return event
