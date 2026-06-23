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
    """Cap for FREE time loans (temporary access). Default 72h, env-configurable."""
    raw = os.environ.get("HOBERADIUS_MAX_LOAN_HOURS", "72")
    try:
        return max(1, int(raw)) * 60
    except ValueError:
        return 72 * 60


def _max_debt_loan_minutes() -> int:
    """Cap for DEBT loans (recorded credit / money owed). A debt loan isn't a
    free giveaway, so it isn't bound by the free-loan cap — only by a generous
    sanity limit (default 366 days) to catch typos. Env: HOBERADIUS_MAX_DEBT_LOAN_DAYS."""
    raw = os.environ.get("HOBERADIUS_MAX_DEBT_LOAN_DAYS", "366")
    try:
        return max(1, int(raw)) * 24 * 60
    except ValueError:
        return 366 * 24 * 60


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

    أسعار المدراء (migration 098): إن كان المشترك تابعًا لمدير
    (subscribers.manager_id) وللمدير سعر خاص متفق عليه على هذا العرض
    (admin_plan_prices)، يُستخدم سعر المدير بدل سعر العرض الرسمي —
    هكذا «يدفع المدير سعره» عند تفعيل/تجديد مشتركيه بهذا العرض.
    الأولوية الكاملة: custom_price للمشترك (الأخصّ) > سعر المدير > سعر العرض.
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
    # نقطة ربط أسعار المدراء: تجاوز (مدير × عرض) قبل السقوط لسعر العرض.
    # القراءة آمنة تمامًا — أي فشل يرجع 0.0 فنكمل بالسعر الافتراضي.
    manager_id = _get(subscriber, "manager_id")
    plan_id = _get(plan, "id") or _get(subscriber, "plan_id")
    if manager_id and plan_id:
        from .admin_pricing import manager_plan_price_override

        tenant_id = _get(subscriber, "tenant_id") or _get(plan, "tenant_id") or 1
        override = manager_plan_price_override(tenant_id, manager_id, plan_id)
        if override > 0:
            return override
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
            raise RadiusValidationError("المشترك غير موجود.")
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
        # دين الرصيد السالب الذي اختار الموظف تسويته من هذه الدفعة لا يشتري مدة.
        # المسار يسوي الرصيد ويسجل قيدًا موازنًا لاحقًا؛ هنا نخصمه فقط من أساس الوقت.
        balance_settled = _to_float(
            body.get("balance_settled_total") or 0, field="balance_settled_total", minimum=0
        )
        time_amount = max(amount - settled_deduction - balance_settled, 0.0)
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
                "balance_settled_total": balance_settled,
                "activation_application": "not_applied_in_foundation_slice",
            },
        )
        # إشعار المشترك باستلام دفعته عبر المحرّك الموحّد notifications_engine
        # (مصدر إعدادات/تسليم إشعارات المشترك). محصّن — لا يكسر التحصيل.
        try:
            from .notifications_engine import notify_event, find_subscriber
            _sub_obj = find_subscriber(self.tenant_id,
                                       subscriber_id=int(subscriber.get("id") or 0),
                                       username=str(subscriber.get("username") or ""))
            notify_event("payment_received", tenant_id=self.tenant_id,
                         subscriber=_sub_obj, context={"amount": amount})
        except Exception:  # noqa: BLE001
            pass
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
                # amount-only loans carry a recorded value → debt → sanity cap.
                duration_minutes = min(derived, _max_debt_loan_minutes())
        if duration_minutes <= 0:
            duration_minutes = _to_int(
                body.get("duration_minutes"),
                field="duration_minutes",
                minimum=1,
            )
        # A FREE loan (temporary access, no recorded value) keeps the strict
        # operator cap. A DEBT loan is recorded credit (money owed) — not a
        # giveaway — so it's bounded only by a generous sanity limit. This is why
        # «تسجيل دين» can span many days while «مجانية» is capped at the free limit.
        is_debt_loan = _truthy(body.get("price_from_days")) or amount > 0
        cap_minutes = _max_debt_loan_minutes() if is_debt_loan else max_minutes
        if not derived_from_price and duration_minutes > cap_minutes:
            if is_debt_loan:
                raise RadiusValidationError(
                    f"مدة الدين تتجاوز الحدّ الأقصى المعقول ({cap_minutes // (24 * 60)} يومًا)."
                )
            raise RadiusValidationError(
                f"مدة السلفة المجانية تتجاوز الحدّ المسموح ({max_minutes // 60} ساعة) — "
                "للمُدد الأطول استخدم «تسجيل دين (مدين)»."
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

    def settle_preview_total(self, actions: list[dict]) -> float:
        """READ-ONLY: sum of the open 'settle' loans' amounts — no mutation.

        Lets the payment route compute the time-basis deduction and create the
        payment FIRST; loans are only actually settled AFTER the payment succeeds,
        so a failed payment never leaves orphaned settlements.
        """
        total = 0.0
        for action in actions or []:
            if str(action.get("action") or "").strip() != "settle":
                continue
            try:
                loan_id = int(action.get("loan_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            loan = accounting_repo.get_loan(self.tenant_id, loan_id)
            if loan and loan.get("status") == "open":
                total += float(loan.get("amount") or 0)
        return round(total, 2)

    def price_basis(self, subscriber) -> dict:
        """Effective price + time-basis for a subscriber — feeds the finance
        modals' auto-price and day-coverage. minutes falls back to a 30-day month
        so quota plans (no duration_minutes) still price."""
        def _get(key):
            return subscriber.get(key) if isinstance(subscriber, dict) else getattr(subscriber, key, None)
        pid = _get("plan_id")
        plan = accounting_repo.resolve_plan(self.tenant_id, int(pid)) if pid else None
        return {
            "price": float(effective_subscriber_price(subscriber, plan) or 0),
            "minutes": int(_base_plan_minutes(plan) or 0) or 43200,
            "custom": bool(float(_get("custom_price") or 0) > 0),
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

    # ── أسماء التقارير وعناوين أعمدتها بالعربية لتصدير PDF الفاخر ──
    # المفاتيح هنا تطابق مفاتيح أعمدة repos/accounting_repo بالضبط؛
    # أي عمود غير معرّف يظهر بمفتاحه الخام كاحتياط (لن يكسر التصدير).
    _PDF_REPORT_TITLES = {
        "daily": "تقرير المبيعات اليومية",
        "monthly": "تقرير المبيعات الشهرية",
        "yearly": "تقرير المبيعات السنوية",
        "subscriber_payments": "تقرير دفعات المستفيدين",
        "loans": "تقرير السلف",
        "activations": "تقرير التفعيلات",
        "card_sales": "تقرير مبيعات الكروت",
        "profit_loss": "تقرير الربح والخسارة",
        "distributor_debts": "تقرير ديون الموزعين",
    }
    _PDF_COLUMN_LABELS = {
        "period": "الفترة",
        "count": "عدد العمليات",
        "total": "الإجمالي",
        "subscriber_id": "رقم المستفيد",
        "username": "اسم المستخدم",
        "last_entry_at": "آخر حركة",
        "status": "الحالة",
        "duration_minutes": "الدقائق",
        "activation_count": "عدد التفعيلات",
        "earned_minutes": "الدقائق المكتسبة",
        "batch_id": "رقم الحزمة",
        "credits": "الإيرادات (دائن)",
        "debits": "المصروفات (مدين)",
        "net": "الصافي",
        "entries": "عدد القيود",
        "source": "المصدر",
        "distributor_id": "رقم الموزع",
        "name": "اسم الموزع",
        "display_name": "الاسم المعروض",
        "debt_balance": "رصيد الدين",
        "balance": "الرصيد",
        "credit_limit": "سقف الائتمان",
    }
    # الأعمدة المالية تُنسَّق كمبالغ (فواصل آلاف + منزلتان عشريتان)
    _PDF_MONEY_COLUMNS = {
        "total", "credits", "debits", "net",
        "debt_balance", "balance", "credit_limit",
    }
    # الأعمدة العددية تُنسَّق بفواصل آلاف بدون كسور
    _PDF_COUNT_COLUMNS = {
        "count", "entries", "activation_count",
        "earned_minutes", "duration_minutes",
    }

    def report_pdf(self, *, report_type: str) -> bytes:
        """تصدير PDF فاخر بهوية HobeRadius — خط Cairo عربي، جدول RTL
        بصفوف زيبرا ورأس بنفسجي، صف إجماليات مميّز، ورأس/تذييل موحّد.
        """
        from .pdf_theme import (
            ar, build_premium_pdf, empty_state, fmt_int, fmt_money,
            kpi_row, content_width, styled_table,
        )
        from reportlab.platypus import Spacer

        items, columns = self._report_export_rows(report_type=report_type)
        title = self._PDF_REPORT_TITLES.get(report_type, f"تقرير {report_type}")
        subtitle = f"عدد السجلات: {len(items)}"

        story: list = []
        if not items:
            story.append(empty_state())
        else:
            headers = [self._PDF_COLUMN_LABELS.get(c, c) for c in columns]

            def _cell(column: str, value) -> str:
                if value is None or value == "":
                    return "—"
                if column in self._PDF_MONEY_COLUMNS:
                    return fmt_money(value)
                if column in self._PDF_COUNT_COLUMNS:
                    return fmt_int(value)
                return str(self._export_value(value))

            rows = [
                [_cell(column, item.get(column)) for column in columns]
                for item in items
            ]

            # صف الإجماليات: نجمع الأعمدة المالية/العددية فقط، وأول
            # عمود يحمل وسم «الإجمالي». تقرير الربح/الخسارة صف واحد
            # أصلًا فلا يحتاج صف إجماليات.
            totals_row = None
            summable = [
                c for c in columns
                if c in self._PDF_MONEY_COLUMNS or c in self._PDF_COUNT_COLUMNS
            ]
            if summable and len(items) > 1:
                sums: dict[str, float] = {}
                for column in summable:
                    sums[column] = sum(
                        float(item.get(column) or 0) for item in items
                    )
                totals_row = []
                for index, column in enumerate(columns):
                    if index == 0 and column not in sums:
                        totals_row.append("الإجمالي")
                    elif column in self._PDF_MONEY_COLUMNS:
                        totals_row.append(fmt_money(sums[column]))
                    elif column in self._PDF_COUNT_COLUMNS:
                        totals_row.append(fmt_int(sums[column]))
                    else:
                        totals_row.append("")

            # بطاقات KPI خفيفة أعلى الجدول (الإجمالي المالي + عدد السجلات)
            kpis: list[tuple[str, str]] = [("عدد السجلات", fmt_int(len(items)))]
            money_cols = [c for c in columns if c in self._PDF_MONEY_COLUMNS]
            for column in money_cols[:3]:
                kpis.append((
                    self._PDF_COLUMN_LABELS.get(column, column),
                    fmt_money(sum(float(item.get(column) or 0) for item in items)),
                ))
            story.append(kpi_row(kpis, page_width=content_width(landscape_mode=True)))
            story.append(Spacer(1, 14))
            story.append(styled_table(headers, rows, totals_row=totals_row))

        return build_premium_pdf(
            title=title,
            subtitle=subtitle,
            story=story,
            landscape_mode=True,
            footer_note="HobeRadius • التقارير المالية",
        )

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

    # ── أعمدة التاريخ المرشّحة لفلترة نطاق اللقطة — بترتيب الأفضلية ──
    # «period» في تقارير المبيعات (يوم/شهر/سنة)، و«last_entry_at» في دفعات
    # المستفيدين، و«created_at/day» احتياطًا لأي تقرير مستقبلي. التقارير التي
    # لا تحمل أي عمود تاريخ (السلف بالحالات، الربح/الخسارة، ديون الموزعين…)
    # تُحفَظ كما هي مع تسجيل النطاق المطلوب في سجل اللقطة فقط.
    _SNAPSHOT_DATE_COLUMNS = ("period", "day", "last_entry_at", "created_at")

    @classmethod
    def _filter_rows_by_range(cls, items: list[dict], date_from: str,
                              date_to: str) -> tuple[list[dict], bool]:
        """فلترة صفوف التقرير بنطاق من/إلى قبل تجميد اللقطة.

        المقارنة نصّية على بادئة ISO (YYYY[-MM[-DD]]) فتصلح للفترات الشهرية
        («2026-06» مقابل «2026-06-01») وللطوابع الكاملة معًا: نقصّ القيمتين
        لأقصر طول مشترك ثم نقارن. يعيد (الصفوف المفلترة، هل طُبّق الفلتر؟).
        """
        if not items or (not date_from and not date_to):
            return items, False
        column = next(
            (c for c in cls._SNAPSHOT_DATE_COLUMNS if c in items[0]), None
        )
        if not column:
            # لا عمود تاريخ في هذا التقرير — نحفظ الصفوف كلها كما هي.
            return items, False

        def _in_range(value) -> bool:
            raw = str(value or "").strip()
            if not raw:
                return False
            if date_from:
                n = min(len(raw), len(date_from))
                if raw[:n] < date_from[:n]:
                    return False
            if date_to:
                n = min(len(raw), len(date_to))
                if raw[:n] > date_to[:n]:
                    return False
            return True

        return [row for row in items if _in_range(row.get(column))], True

    # الأعمدة المالية التي يجمعها «إجمالي اللقطة» (أول عمود متوفر منها)
    _SNAPSHOT_TOTAL_COLUMNS = ("total", "amount", "net", "debt_balance")

    @classmethod
    def _snapshot_total(cls, items: list[dict]) -> float | None:
        """مجموع العمود المالي الرئيسي لصفوف اللقطة — None إذا لا عمود مالي."""
        if not items:
            return 0.0
        column = next(
            (c for c in cls._SNAPSHOT_TOTAL_COLUMNS if c in items[0]), None
        )
        if not column:
            return None
        try:
            return round(sum(float(row.get(column) or 0) for row in items), 2)
        except (TypeError, ValueError):
            return None

    def create_report_snapshot(self, *, report_type: str, actor: str = "",
                               date_from: str = "", date_to: str = "",
                               note: str = "",
                               parameters: dict | None = None) -> dict:
        items = self.reports(report_type=report_type)
        # فلترة الصفوف بالنطاق المختار قبل التجميد — اللقطة تحفظ ما طُلب فقط.
        items, range_applied = self._filter_rows_by_range(items, date_from, date_to)
        payload = {
            "items": items,
            "count": len(items),
            "report_type": report_type,
            # نخزّن النطاق والملاحظة والإجمالي داخل النتيجة نفسها حتى تعرضها
            # جداول اللقطات مباشرة (اللقطات القديمة بدونها تعرض «—»).
            "date_from": date_from,
            "date_to": date_to,
            "note": note,
            "total": self._snapshot_total(items),
            "range_applied": range_applied,
        }
        params = dict(parameters or {})
        if note:
            params["note"] = note
        return accounting_repo.create_report_snapshot(
            self.tenant_id,
            report_type=report_type,
            result=payload,
            created_by=actor,
            date_from=date_from,
            date_to=date_to,
            parameters=params,
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
