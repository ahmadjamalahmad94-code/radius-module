"""Accounting + loans foundation service."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from typing import Any

from ..core.errors import RadiusValidationError
from ..db.helpers import dt_to_iso, json_load
from ..db.repos import accounting_repo
from .radius_apply import apply_activation_minutes


def _to_float(value: Any, *, field: str, minimum: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{field} must be a number") from None
    if out < minimum:
        raise RadiusValidationError(f"{field} must be >= {minimum}")
    return out


def _to_int(value: Any, *, field: str, minimum: int = 0) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{field} must be an integer") from None
    if out < minimum:
        raise RadiusValidationError(f"{field} must be >= {minimum}")
    return out


def _max_loan_minutes() -> int:
    raw = os.environ.get("HOBERADIUS_MAX_LOAN_HOURS", "72")
    try:
        return max(1, int(raw)) * 60
    except ValueError:
        return 72 * 60


def _base_plan_minutes(plan: dict | None) -> int:
    if not plan:
        return 0
    if int(plan.get("duration_minutes") or 0) > 0:
        return int(plan["duration_minutes"])
    if int(plan.get("validity_days") or 0) > 0:
        return int(plan["validity_days"]) * 24 * 60
    value = int(plan.get("duration_value") or 0)
    unit = (plan.get("duration_unit") or "").lower()
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


def calculate_proportional_minutes(
    *,
    amount_paid: float,
    plan_price: float,
    base_minutes: int,
    rounding_mode: str = "floor",
) -> int:
    if amount_paid <= 0 or plan_price <= 0 or base_minutes <= 0:
        return 0
    raw = base_minutes * (amount_paid / plan_price)
    if rounding_mode == "ceil":
        return int(math.ceil(raw))
    if rounding_mode == "nearest":
        return int(round(raw))
    return int(math.floor(raw))


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


class AccountingService:
    def __init__(self, tenant_id: int) -> None:
        self.tenant_id = tenant_id

    def resolve_subscriber(self, body: dict) -> dict:
        subscriber = accounting_repo.resolve_subscriber(
            self.tenant_id,
            subscriber_id=body.get("subscriber_id"),
            username=str(body.get("username") or "").strip(),
        )
        if not subscriber:
            raise RadiusValidationError("subscriber not found")
        return subscriber

    def list_ledger(self, *, entry_type: str = "", subscriber_id: int | None = None,
                    limit: int = 100, offset: int = 0) -> list[dict]:
        return accounting_repo.list_ledger_entries(
            self.tenant_id,
            entry_type=entry_type,
            subscriber_id=subscriber_id,
            limit=limit,
            offset=offset,
        )

    def void_ledger(self, *, entry_id: int, actor: str, reason: str = "") -> dict:
        entry = accounting_repo.void_ledger_entry(
            tenant_id=self.tenant_id,
            entry_id=entry_id,
            actor=actor,
            reason=reason,
        )
        if not entry:
            raise RadiusValidationError("ledger entry not found")
        return entry

    def create_payment(self, body: dict, *, actor: str,
                       distributor_id: int | None = None) -> dict:
        subscriber = self.resolve_subscriber(body)
        plan_id = body.get("plan_id") or subscriber.get("plan_id")
        plan = accounting_repo.resolve_plan(self.tenant_id, int(plan_id)) if plan_id else None

        amount = _to_float(body.get("amount"), field="amount", minimum=0.01)
        currency = str(body.get("currency") or (plan or {}).get("currency") or "JOD").upper()[:8]
        method = str(body.get("method") or "cash")[:40]
        notes = str(body.get("notes") or "")[:500]
        rounding = str(body.get("rounding_mode") or "floor")
        if rounding not in {"floor", "ceil", "nearest"}:
            raise RadiusValidationError("rounding_mode must be floor, ceil, or nearest")

        default_price = float((plan or {}).get("price") or 0)
        custom_price = body.get("custom_price")
        custom_price_f = None
        if custom_price not in (None, ""):
            custom_price_f = _to_float(custom_price, field="custom_price", minimum=0.01)
        discount = _to_float(body.get("discount_amount") or 0, field="discount_amount", minimum=0)
        plan_price = custom_price_f if custom_price_f is not None else default_price
        effective_price = max(plan_price - discount, 0)
        base_minutes = _base_plan_minutes(plan)
        earned_minutes = calculate_proportional_minutes(
            amount_paid=amount,
            plan_price=effective_price,
            base_minutes=base_minutes,
            rounding_mode=rounding,
        )

        payment = accounting_repo.create_payment(
            tenant_id=self.tenant_id,
            subscriber=subscriber,
            plan=plan,
            amount=amount,
            currency=currency,
            method=method,
            created_by=actor,
            plan_price=default_price,
            custom_price=custom_price_f,
            discount_amount=discount,
            discount_reason=str(body.get("discount_reason") or "")[:300],
            effective_price=effective_price,
            earned_minutes=earned_minutes,
            rounding_mode=rounding,
            notes=notes,
            distributor_id=distributor_id,
            metadata={
                "base_minutes": base_minutes,
                "activation_application": "not_applied_in_foundation_slice",
            },
        )
        apply_requested = _truthy(body.get("apply_to_radius"))
        dry_run = _truthy(body.get("dry_run"))
        activation_result = {
            "applied_to_radius": False,
            "dry_run": dry_run,
            "source": "payment",
            "status": "skipped",
            "reason": "apply_to_radius was not requested",
        }
        if apply_requested and earned_minutes > 0:
            activation_result = apply_activation_minutes(
                username=subscriber["username"],
                minutes=earned_minutes,
                actor=actor,
                source=f"payment:{payment['id']}",
                dry_run=dry_run,
            )
        elif apply_requested:
            activation_result.update({
                "status": "skipped",
                "reason": "earned_minutes is 0",
            })
        payment["proportional_activation"] = {
            "base_minutes": base_minutes,
            "earned_minutes": earned_minutes,
            "rounding_mode": rounding,
            "applied_to_radius": bool(activation_result.get("applied_to_radius")),
        }
        payment["activation_result"] = activation_result
        payment["radius_action_id"] = activation_result.get("radius_action_id")
        payment["dry_run"] = dry_run
        return payment

    def list_payments(self, *, subscriber_id: int | None = None,
                      distributor_id: int | None = None,
                      limit: int = 100, offset: int = 0) -> list[dict]:
        return accounting_repo.list_payments(
            self.tenant_id,
            subscriber_id=subscriber_id,
            distributor_id=distributor_id,
            limit=limit,
            offset=offset,
        )

    def create_loan(self, body: dict, *, actor: str) -> dict:
        subscriber = self.resolve_subscriber(body)
        hours = body.get("hours")
        days = body.get("days")
        duration_minutes = 0
        if hours not in (None, ""):
            duration_minutes += _to_int(hours, field="hours", minimum=0) * 60
        if days not in (None, ""):
            duration_minutes += _to_int(days, field="days", minimum=0) * 24 * 60
        if duration_minutes <= 0:
            duration_minutes = _to_int(
                body.get("duration_minutes"),
                field="duration_minutes",
                minimum=1,
            )
        max_minutes = _max_loan_minutes()
        if duration_minutes > max_minutes:
            raise RadiusValidationError(
                f"loan duration exceeds configured limit ({max_minutes // 60} hours)"
            )
        amount = _to_float(body.get("amount") or 0, field="amount", minimum=0)
        now = datetime.utcnow()
        loan = accounting_repo.create_loan(
            tenant_id=self.tenant_id,
            subscriber=subscriber,
            duration_minutes=duration_minutes,
            amount=amount,
            currency=str(body.get("currency") or "JOD").upper()[:8],
            reason=str(body.get("reason") or "")[:500],
            created_by=actor,
            starts_at=dt_to_iso(now),
            ends_at=dt_to_iso(now + timedelta(minutes=duration_minutes)),
            max_limit_snapshot=max_minutes,
            metadata={
                "approval_required": False,
                "activation_application": "not_applied_in_foundation_slice",
            },
        )
        apply_requested = _truthy(body.get("apply_to_radius"))
        dry_run = _truthy(body.get("dry_run"))
        activation_result = {
            "applied_to_radius": False,
            "dry_run": dry_run,
            "source": "loan",
            "status": "skipped",
            "reason": "apply_to_radius was not requested",
        }
        if apply_requested:
            activation_result = apply_activation_minutes(
                username=subscriber["username"],
                minutes=duration_minutes,
                actor=actor,
                source=f"loan:{loan['id']}",
                dry_run=dry_run,
            )
        loan["activation_window"] = {
            "starts_at": loan["starts_at"],
            "ends_at": loan["ends_at"],
            "duration_minutes": duration_minutes,
            "applied_to_radius": bool(activation_result.get("applied_to_radius")),
        }
        loan["activation_result"] = activation_result
        loan["radius_action_id"] = activation_result.get("radius_action_id")
        loan["dry_run"] = dry_run
        return loan

    def list_loans(self, *, status: str = "", subscriber_id: int | None = None,
                   limit: int = 100, offset: int = 0) -> list[dict]:
        return accounting_repo.list_loans(
            self.tenant_id,
            status=status,
            subscriber_id=subscriber_id,
            limit=limit,
            offset=offset,
        )

    def get_loan(self, loan_id: int) -> dict:
        loan = accounting_repo.get_loan(self.tenant_id, loan_id)
        if not loan:
            raise RadiusValidationError("loan not found")
        return loan

    def settle_loan(self, loan_id: int, body: dict, *, actor: str) -> dict:
        loan = self.get_loan(loan_id)
        if loan["status"] != "open":
            raise RadiusValidationError("loan is not open")
        amount = _to_float(body.get("amount") or loan.get("amount") or 0,
                           field="amount", minimum=0)
        settlement = accounting_repo.settle_loan(
            tenant_id=self.tenant_id,
            loan=loan,
            amount=amount,
            currency=str(body.get("currency") or loan.get("currency") or "JOD").upper()[:8],
            method=str(body.get("method") or "manual")[:40],
            created_by=actor,
            notes=str(body.get("notes") or "")[:500],
            metadata={"settlement_type": str(body.get("settlement_type") or "manual")},
        )
        return settlement

    def reports(self, *, report_type: str) -> list[dict]:
        if report_type in {"daily", "monthly", "yearly"}:
            return accounting_repo.sales_summary(self.tenant_id, grain=report_type)
        if report_type == "subscriber_payments":
            return accounting_repo.subscriber_payment_report(self.tenant_id)
        if report_type == "loans":
            return accounting_repo.loan_report(self.tenant_id)
        raise RadiusValidationError("unsupported report type")


def service_from_context() -> AccountingService:
    from flask import g
    return AccountingService(int(getattr(g, "tenant_id", 1)))
