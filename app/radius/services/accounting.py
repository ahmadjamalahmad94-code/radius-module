"""Accounting + loans foundation service."""
from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import datetime, timedelta
from typing import Any

from ..core.errors import RadiusValidationError
from ..core.system_config import default_currency
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


def _coerce_price(value: Any) -> float:
    """Best-effort float for a price-like field; non-numeric / negative → 0.0."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out > 0 else 0.0


def effective_subscriber_price(subscriber: Any, plan: Any) -> float:
    """Single source of truth for "what does this subscriber actually pay?".

    The subscriber's stored ``custom_price`` overrides the plan (offer) price.
    A NULL / 0 / missing custom price falls back to the plan price. Returns 0.0
    when neither is set (callers that divide by price already guard ``<= 0``).

    Accepts either a mapping (sqlite row dict) or a dataclass/object for both
    ``subscriber`` and ``plan`` so every call site — repo dicts in accounting,
    ``Subscriber`` dataclasses in routes — can share the exact same rule.
    """
    def _get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    custom = _coerce_price(_get(subscriber, "custom_price"))
    if custom > 0:
        return custom
    return _coerce_price(_get(plan, "price"))


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


def calculate_proportional_amount(
    *,
    minutes: int,
    plan_price: float,
    base_minutes: int,
    decimals: int = 2,
) -> float:
    """Inverse of calculate_proportional_minutes: the money value of a span of
    time at the subscriber's effective price. ``amount = price × minutes/base``.

    Rounded to ``decimals`` places (operator decision: 2 decimals). Returns 0.0
    on any non-positive input so callers can divide/compare safely.
    """
    if minutes <= 0 or plan_price <= 0 or base_minutes <= 0:
        return 0.0
    return round(plan_price * (minutes / base_minutes), decimals)


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

    def get_payment(self, payment_id: int) -> dict:
        payment = accounting_repo.get_payment(self.tenant_id, payment_id)
        if not payment:
            raise RadiusValidationError("payment not found")
        return payment

    def void_payment(self, *, payment_id: int, actor: str, reason: str = "") -> dict:
        payment = self.get_payment(payment_id)
        if payment.get("status") == "voided":
            raise RadiusValidationError("payment is already voided")
        result = accounting_repo.void_payment(
            tenant_id=self.tenant_id,
            payment=payment,
            actor=actor,
            reason=reason,
        )
        if not result:
            raise RadiusValidationError("payment ledger entry not found")
        return result

    def create_payment(self, body: dict, *, actor: str,
                       distributor_id: int | None = None) -> dict:
        subscriber = self.resolve_subscriber(body)
        plan_id = body.get("plan_id") or subscriber.get("plan_id")
        plan = accounting_repo.resolve_plan(self.tenant_id, int(plan_id)) if plan_id else None

        amount = _to_float(body.get("amount"), field="amount", minimum=0.01)
        currency = str(body.get("currency") or (plan or {}).get("currency") or default_currency()).upper()[:8]
        method = str(body.get("method") or "cash")[:40]
        notes = str(body.get("notes") or "")[:500]
        rounding = str(body.get("rounding_mode") or "floor")
        if rounding not in {"floor", "ceil", "nearest"}:
            raise RadiusValidationError("rounding_mode must be floor, ceil, or nearest")

        # The subscriber's stored custom_price is the OFFICIAL base price for all
        # money math (full payment, partial payment, renewal, loan). It overrides
        # the plan (offer) price. 0 / None falls back to the plan price. A
        # per-transaction custom_price in the request body still wins for that one
        # entry (manual one-off override).
        default_price = effective_subscriber_price(subscriber, plan)
        custom_price = body.get("custom_price")
        custom_price_f = None
        if custom_price not in (None, ""):
            custom_price_f = _to_float(custom_price, field="custom_price", minimum=0.01)
        discount = _to_float(body.get("discount_amount") or 0, field="discount_amount", minimum=0)
        plan_price = custom_price_f if custom_price_f is not None else default_price
        effective_price = max(plan_price - discount, 0)
        base_minutes = _base_plan_minutes(plan)
        # Loans the operator chose to SETTLE from this payment reduce the amount
        # that converts to time — the FULL amount is still recorded as income,
        # but the settled portion clears old debt instead of buying new time.
        settled_deduction = _to_float(
            body.get("loan_settled_total") or 0, field="loan_settled_total", minimum=0
        )
        time_amount = max(amount - settled_deduction, 0.0)
        earned_minutes = calculate_proportional_minutes(
            amount_paid=time_amount,
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
                "loan_settled_total": settled_deduction,
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
        amount = _to_float(body.get("amount") or 0, field="amount", minimum=0)
        duration_minutes = 0
        if hours not in (None, ""):
            duration_minutes += _to_int(hours, field="hours", minimum=0) * 60
        if days not in (None, ""):
            duration_minutes += _to_int(days, field="days", minimum=0) * 24 * 60
        # Operator-picks-DAYS flow: when the modal prices the loan from its
        # duration (price_from_days), derive the loan VALUE from the subscriber's
        # effective price (offer/custom), rounded to 2 decimals (operator choice).
        if _truthy(body.get("price_from_days")) and duration_minutes > 0:
            _plan = (
                accounting_repo.resolve_plan(self.tenant_id, int(subscriber["plan_id"]))
                if subscriber.get("plan_id") else None
            )
            amount = calculate_proportional_amount(
                minutes=duration_minutes,
                plan_price=effective_subscriber_price(subscriber, _plan),
                base_minutes=_base_plan_minutes(_plan),
            )
        max_minutes = _max_loan_minutes()
        # Explicit time always wins. If none was given, derive the loaned time
        # PROPORTIONALLY from the subscriber's official price (custom_price, else
        # plan price) - same rule as a partial payment. e.g. custom 150 for 30
        # days, loan amount 75 -> 15 days. A price-derived duration is clamped to
        # the loan cap (operator gave money, not an out-of-range time).
        derived_from_price = False
        if duration_minutes <= 0 and amount > 0:
            plan = (
                accounting_repo.resolve_plan(self.tenant_id, int(subscriber["plan_id"]))
                if subscriber.get("plan_id") else None
            )
            official_price = effective_subscriber_price(subscriber, plan)
            derived = calculate_proportional_minutes(
                amount_paid=amount,
                plan_price=official_price,
                base_minutes=_base_plan_minutes(plan),
                rounding_mode="floor",
            )
            if derived > 0:
                derived_from_price = True
                duration_minutes = min(derived, max_minutes)
        if duration_minutes <= 0:
            duration_minutes = _to_int(
                body.get("duration_minutes"),
                field="duration_minutes",
                minimum=1,
            )
        if not derived_from_price and duration_minutes > max_minutes:
            raise RadiusValidationError(
                f"loan duration exceeds configured limit ({max_minutes // 60} hours)"
            )
        now = datetime.utcnow()
        loan = accounting_repo.create_loan(
            tenant_id=self.tenant_id,
            subscriber=subscriber,
            duration_minutes=duration_minutes,
            amount=amount,
            currency=str(body.get("currency") or default_currency()).upper()[:8],
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
            currency=str(body.get("currency") or loan.get("currency") or default_currency()).upper()[:8],
            method=str(body.get("method") or "manual")[:40],
            created_by=actor,
            notes=str(body.get("notes") or "")[:500],
            metadata={"settlement_type": str(body.get("settlement_type") or "manual")},
        )
        return settlement

    def writeoff_loan(self, loan_id: int, *, actor: str, notes: str = "") -> dict:
        loan = self.get_loan(loan_id)
        if loan["status"] != "open":
            raise RadiusValidationError("loan is not open")
        return accounting_repo.writeoff_loan(
            tenant_id=self.tenant_id,
            loan=loan,
            currency=str(loan.get("currency") or default_currency()).upper()[:8],
            created_by=actor,
            notes=(notes or "مسامحة سلفة")[:500],
        )

    def open_loans_for(self, *, subscriber_id: int) -> list[dict]:
        """Open loans for a subscriber, each annotated with its day-equivalent
        (duration_minutes / 1440) so the UI can show «٣ أيام / ٩ ₪»."""
        loans = accounting_repo.list_loans(
            self.tenant_id, status="open", subscriber_id=subscriber_id, limit=100,
        )
        for ln in loans:
            ln["days"] = round(int(ln.get("duration_minutes") or 0) / 1440.0, 2)
        return loans

    def resolve_loan_actions(self, actions: list[dict], *, actor: str) -> dict:
        """Apply per-loan operator choices from the payment/balance modal.

        Each action = {loan_id, action: 'settle'|'writeoff'} ('defer'/unknown =
        left open). Returns {settled_total, settled_ids, writeoff_ids} so the
        caller can DEDUCT settled_total from an incoming payment's time-basis.
        """
        settled_total = 0.0
        settled_ids: list[int] = []
        writeoff_ids: list[int] = []
        for action in actions or []:
            try:
                loan_id = int(action.get("loan_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            kind = str(action.get("action") or "").strip()
            loan = accounting_repo.get_loan(self.tenant_id, loan_id)
            if not loan or loan.get("status") != "open":
                continue
            currency = str(loan.get("currency") or default_currency()).upper()[:8]
            if kind == "settle":
                amt = float(loan.get("amount") or 0)
                accounting_repo.settle_loan(
                    tenant_id=self.tenant_id, loan=loan, amount=amt,
                    currency=currency, method="payment", created_by=actor,
                    notes="تسوية مع دفعة", metadata={"settlement_type": "with_payment"},
                )
                settled_total += amt
                settled_ids.append(loan_id)
            elif kind == "writeoff":
                accounting_repo.writeoff_loan(
                    tenant_id=self.tenant_id, loan=loan, currency=currency,
                    created_by=actor, notes="مسامحة سلفة",
                )
                writeoff_ids.append(loan_id)
        return {
            "settled_total": round(settled_total, 2),
            "settled_ids": settled_ids,
            "writeoff_ids": writeoff_ids,
        }

    def reports(self, *, report_type: str) -> list[dict]:
        if report_type in {"daily", "monthly", "yearly"}:
            return accounting_repo.sales_summary(self.tenant_id, grain=report_type)
        if report_type == "subscriber_payments":
            return accounting_repo.subscriber_payment_report(self.tenant_id)
        if report_type == "loans":
            return accounting_repo.loan_report(self.tenant_id)
        if report_type == "activations":
            return accounting_repo.activation_report(self.tenant_id)
        if report_type == "card_sales":
            return accounting_repo.card_sales_report(self.tenant_id)
        if report_type == "profit_loss":
            return accounting_repo.profit_loss_summary(self.tenant_id)
        if report_type == "distributor_debts":
            return accounting_repo.distributor_debts_report(self.tenant_id)
        raise RadiusValidationError("unsupported report type")

    def report_csv(self, *, report_type: str) -> str:
        items, columns = self._report_export_rows(report_type=report_type)
        if not items:
            return "\ufeff"
        out = io.StringIO()
        out.write("\ufeff")
        writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {
                column: self._export_value(item.get(column))
                for column in columns
            }
            for item in items
        )
        return out.getvalue()

    def report_xlsx(self, *, report_type: str) -> bytes:
        from openpyxl import Workbook

        items, columns = self._report_export_rows(report_type=report_type)
        out = io.BytesIO()
        wb = Workbook()
        ws = wb.active
        ws.title = "report"
        if columns:
            ws.append(columns)
        for item in items:
            ws.append([self._export_value(item.get(column)) for column in columns])
        wb.save(out)
        return out.getvalue()

    def report_pdf(self, *, report_type: str) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        items, columns = self._report_export_rows(report_type=report_type)
        out = io.BytesIO()
        doc = SimpleDocTemplate(
            out,
            pagesize=landscape(A4),
            rightMargin=18,
            leftMargin=18,
            topMargin=18,
            bottomMargin=18,
        )
        styles = getSampleStyleSheet()
        story = [
            Paragraph(f"HobeRadius financial report: {report_type}", styles["Title"]),
            Spacer(1, 10),
        ]
        if not items:
            data = [["No data"]]
        else:
            data = [columns]
            data.extend(
                [str(self._export_value(item.get(column))) for column in columns]
                for item in items
            )
        table = Table(data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ]
            )
        )
        story.append(table)
        doc.build(story)
        return out.getvalue()

    def _report_export_rows(self, *, report_type: str) -> tuple[list[dict], list[str]]:
        items = self.reports(report_type=report_type)
        if not items:
            return [], []
        columns = list(items[0].keys())
        for item in items[1:]:
            for key in item.keys():
                if key not in columns:
                    columns.append(key)
        return items, columns

    @staticmethod
    def _export_value(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def create_report_snapshot(self, *, report_type: str, actor: str = "",
                               date_from: str = "", date_to: str = "",
                               parameters: dict | None = None) -> dict:
        items = self.reports(report_type=report_type)
        payload = {
            "items": items,
            "count": len(items),
            "report_type": report_type,
        }
        return accounting_repo.create_report_snapshot(
            self.tenant_id,
            report_type=report_type,
            result=payload,
            created_by=actor,
            date_from=date_from,
            date_to=date_to,
            parameters=parameters or {},
        )

    def list_report_snapshots(self, *, report_type: str = "",
                              limit: int = 50, offset: int = 0) -> list[dict]:
        return accounting_repo.list_report_snapshots(
            self.tenant_id,
            report_type=report_type,
            limit=limit,
            offset=offset,
        )

    def get_report_snapshot(self, snapshot_id: int) -> dict:
        snapshot = accounting_repo.get_report_snapshot(self.tenant_id, snapshot_id)
        if not snapshot:
            raise RadiusValidationError("report snapshot not found")
        return snapshot


def service_from_context() -> AccountingService:
    from flask import g
    return AccountingService(int(getattr(g, "tenant_id", 1)))
