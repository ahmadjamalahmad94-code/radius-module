"""Accounting + loans foundation service."""
from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from ..core.errors import RadiusConflict, RadiusNotFound, RadiusValidationError
from ..core.numbers import (
    MONEY_MAX,
    NonFiniteNumber, action_amount, field_label, normalize_number_text, round_money,
)
from ..core.system_config import default_currency
from ..db.connection import after_commit, atomic
from ..db.helpers import dt_to_iso, json_load
from ..db.repos import accounting_repo
from .radius_apply import apply_activation_minutes, revoke_activation_minutes


def normalize_currency(value: Any, *, default: str = "") -> str:
    """رمز عملة مُتحقَّق منه (ISO من قائمة العملات المدعومة) — فارغ ⇒ عملة
    النظام (عملة الرصيد). رمزٌ مجهول (``XYZ``/``btc😀``) يُرفض بـ422 بدل أن
    يُخزَّن حرفيًّا ويخلط التقارير."""
    from ..core.system_config import CURRENCY_SYMBOLS
    code = str(value or "").strip().upper()
    if not code:
        return str(default or default_currency()).strip().upper()[:8]
    if code not in CURRENCY_SYMBOLS:
        raise RadiusValidationError(
            f"العملة «{code[:12]}» غير مدعومة. العملات المدعومة: "
            + "، ".join(sorted(CURRENCY_SYMBOLS)) + ".")
    return code


# قيود دفترٍ حرّكت رصيد المشترك (subscribers.balance) — عكسُها يُعيد الرصيد.
_WALLET_SOURCES = frozenset({
    "subscriber_cash_balance",        # إضافة رصيد نقديّ (دائن +)
    "payment_balance_settlement",     # تسديد دين رصيدٍ سالب من دفعة (دائن +)
    "subscriber_time_extension",      # تمديد مدفوع/على الدين (مدين −)
    "subscriber_quota_topup",         # كوتة مدفوعة/على الدين (مدين −)
    "subscriber_daily_quota_reset",   # استعادة كوتة يوميّة مدفوعة/دين (مدين −)
    "subscriber_plan_change",         # دين فرق تغيير العرض (مدين −)
})


def _loan_id_of(action: Any) -> int | None:
    try:
        return int(action.get("loan_id"))
    except (TypeError, ValueError, AttributeError):
        return None


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _to_float(value: Any, *, field: str, minimum: float = 0.0) -> float:
    # الرسائل عربيّة من المصدر (كانت «amount must be >= 0.01» تصل للويب خامًا).
    if isinstance(value, bool):
        # ‎"amount": true كان يُقرأ 1.00 (تسوية/دفعة بقيمة 1).
        raise NonFiniteNumber(f"قيمة «{field_label(field)}» يجب أن تكون رقمًا.",
                              details={"field": field})
    try:
        # «٣٫٢٥» / «١٬٠٠٠» — the shared parser (F03 N6: the loans centre and
        # settle refused the Arabic decimal the rest of the API accepts).
        out = float(normalize_number_text(value))
    except (TypeError, ValueError, OverflowError):
        raise NonFiniteNumber(f"قيمة «{field_label(field)}» يجب أن تكون رقمًا.",
                              details={"field": field}) from None
    if not math.isfinite(out) or abs(out) > MONEY_MAX:
        # «nan» كان يمرّ (NaN < minimum خطأ) ثم يُسقط الإدراج بـ 500.
        raise NonFiniteNumber(f"قيمة «{field_label(field)}» يجب أن تكون رقمًا منتهيًا صالحًا.",
                              details={"field": field})
    if out < minimum:
        raise NonFiniteNumber(
            f"قيمة «{field_label(field)}» يجب ألّا تقلّ عن {_fmt_num(minimum)}.",
            details={"field": field})
    return out


def _to_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise NonFiniteNumber(f"قيمة «{field_label(field)}» يجب أن تكون عددًا صحيحًا.",
                              details={"field": field})
    try:
        out = int(normalize_number_text(value))
    except (TypeError, ValueError, OverflowError):
        raise NonFiniteNumber(f"قيمة «{field_label(field)}» يجب أن تكون عددًا صحيحًا.",
                              details={"field": field}) from None
    if out < minimum:
        raise NonFiniteNumber(
            f"قيمة «{field_label(field)}» يجب ألّا تقلّ عن {minimum}.",
            details={"field": field})
    return out


# ── تنبيهات الإدارة — محصّنة، لا تكسر التحصيل/السلفة أبدًا ────────────────
def _notify_admin_alert(tenant_id, key: str, context: dict, *,
                        dedup_key: str = "") -> None:
    # بعد COMMIT فقط: لا تنبيه بدفعة/سلفة رجعت معاملتها، ولا شبكة تحت قفل الكتابة.
    def _send() -> None:
        try:
            from .admin_alerts import dispatch
            dispatch(int(tenant_id or 1), key, context, dedup_key=dedup_key)
        except Exception:  # noqa: BLE001
            pass
    after_commit(_send)


_PAYMENT_METHOD_AR: dict[str, str] = {
    "cash": "نقدًا", "bank": "تحويل بنكي", "bank_transfer": "تحويل بنكي",
    "transfer": "تحويل", "card": "بطاقة", "wallet": "محفظة",
    "jawwal": "محفظة جوّال", "jawwal_pay": "محفظة جوّال",
    "online": "دفع إلكترونيّ", "cheque": "شيك", "check": "شيك",
}


def _payment_method_ar(method: str) -> str:
    m = str(method or "").strip().lower()
    return _PAYMENT_METHOD_AR.get(m, method or "—")


def _fmt_minutes_ar(minutes) -> str:
    """دقائق → مدّة عربية مقروءة («يومان (2880 دقيقة)»). آمن دائمًا."""
    try:
        m = int(minutes or 0)
    except (TypeError, ValueError):
        return "—"
    if m <= 0:
        return "—"
    days, rem = divmod(m, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if mins:
        parts.append(f"{mins} دقيقة")
    human = " و".join(parts) if parts else f"{m} دقيقة"
    return f"{human} ({m} دقيقة)" if (days or hours) else human


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
    sanity limit to catch typos. Env: HOBERADIUS_MAX_DEBT_LOAN_DAYS.

    قرار المالك (2026-09-29): أقصى إضافة وقتٍ في العمليّة الواحدة سنة — فالسقف
    لا يتجاوز 365 يومًا مهما كانت قيمة البيئة (كان الافتراضيّ 366)."""
    from ..core import limits
    cap_days = limits.max_extend_days()   # «الحدود» — أقصى أيام في المرة (الافتراض 365)
    raw = os.environ.get("HOBERADIUS_MAX_DEBT_LOAN_DAYS", str(cap_days))
    try:
        days = max(1, int(raw))
    except ValueError:
        days = cap_days
    return min(days, cap_days) * 24 * 60


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
    return out if math.isfinite(out) and out > 0 else 0.0


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
    # حسابٌ **دقيق** بالكسور العشريّة كما تُكتب (لا فاصلة عائمة): كان
    # 1440 × (7 ÷ 5) = 2015.9999… فيُقطع إلى 2015 بدل 2016 (دقيقة ضائعة في
    # 0.2–2.7% من المبالغ بالقروش).
    raw = (Fraction(int(base_minutes)) * _exact(amount_paid)) / _exact(plan_price)
    if rounding_mode == "ceil":
        return int(math.ceil(raw))
    if rounding_mode == "nearest":
        # نصفٌ للأعلى (نفس قاعدة المال) — لا تقريب المصرفيّين.
        return int(math.floor(raw + Fraction(1, 2)))
    return int(math.floor(raw))


def _exact(value: Any) -> Fraction:
    """القيمة العشريّة ككسرٍ دقيق — 0.1 تعني 1/10. التقريب لستّ خانات يمحو
    ضجيج الطرح العائم (20 − 16.67 = 3.3299999999999983 ⇒ 3.33)."""
    return Fraction(Decimal(repr(round(float(value), 6))))


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

    🔑 **الدالّة الوحيدة لسعر مدّة** (API/التطبيق/الويب المفرد/الجماعيّ/السلفة):
    السعر بالقروش × الدقائق ÷ دقائق الأساس، مقرّبًا **نصفًا للأعلى** بحسابٍ
    صحيحٍ دقيق. كان ``round()`` على فاصلةٍ عائمة يعطي 0.62 (API والجماعيّ)
    بينما يعطي الويب والتطبيق 0.63 لـ180 دقيقة على 5/يوم. جافاسكربت الويب
    (``hrPriceOfMinutes``) تطبّق المعادلة الصحيحة نفسها.
    """
    if minutes <= 0 or plan_price <= 0 or base_minutes <= 0:
        return 0.0
    scale = 10 ** int(decimals)
    price_units = int((Decimal(repr(float(plan_price))) * scale).to_integral_value(
        rounding=ROUND_HALF_UP))
    q, r = divmod(price_units * int(minutes), int(base_minutes))
    if 2 * r >= int(base_minutes):
        q += 1
    return q / scale


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

    @atomic
    def void_ledger(self, *, entry_id: int, actor: str, reason: str = "") -> dict:
        """قيدٌ عكسيّ واحد لكل قيد (409 للثاني، 422 لعكس قيدٍ عكسيّ).

        قيد **دفعة** يُلغى عبر مسار إلغاء الدفعة نفسه: تُعلَّم الدفعة «voided»
        ويُسترجع وقتها — فلا يبقى في قائمة الدفعات «posted» وهو معكوس."""
        original = accounting_repo.get_ledger_entry(self.tenant_id, entry_id)
        if not original:
            raise RadiusNotFound("القيد غير موجود.")
        if (original.get("entry_type") == "payment"
                and original.get("source_type") == "payment"
                and original.get("source_id")
                and not accounting_repo.is_reversal_entry(original)):
            payment = accounting_repo.get_payment(self.tenant_id, int(original["source_id"]))
            if payment and int(payment.get("ledger_entry_id") or 0) == int(entry_id):
                return self.void_payment(payment_id=int(payment["id"]), actor=actor,
                                         reason=reason)["entry"]
        source = str(original.get("source_type") or "")
        if accounting_repo.is_reversal_entry(original):
            raise RadiusValidationError("لا يمكن عكس قيدٍ عكسيّ.")
        if accounting_repo.ledger_entry_reversed(self.tenant_id, entry_id):
            raise RadiusConflict("هذا القيد معكوسٌ (مُلغى) مسبقًا.")
        # ── موجة الإصلاح 2: العكس يعكس **الأثر** لا القيد وحده ──
        # كان عكس قيد رصيدٍ نقديّ يكتب قيدًا عكسيًّا ويترك الرصيد كما هو (فلا
        # يطابق الرصيدُ الدفتر)، وعكس قيد سلفة يتركها مفتوحةً بوقتها. القاعدة:
        #   • سلفة: تُلغى (status=voided) ويُسترجع وقتها المُطبَّق — وتُرفض 409
        #     إن كانت لها تسويات مُرحَّلة أو أُغلقت (اعكس تسوياتها أوّلًا).
        #   • تسوية سلفة: تُلغى التسوية وتعود السلفة مفتوحةً بمتبقّيها.
        #   • مسامحة: تعود السلفة مفتوحةً بمتبقّيها.
        #   • قيود محفظة المشترك (رصيد نقديّ، تسديد دين، تمديد مدفوع/دين،
        #     كوتة، دين تغيير العرض): يُعاد الرصيد كما كان؛ وقيد تمديد الوقت
        #     يسترجع دقائقه أيضًا (لا تحت «الآن»). الكوتة/العرض لا تُمسّ.
        #   • غير ذلك (قيود دفتريّة بلا أثرٍ على مشترك): القيد العكسيّ وحده.
        if source == "loan" and original.get("source_id"):
            return self._void_loan_entry(original, actor=actor, reason=reason)
        if source == "settlement" and original.get("source_id"):
            return self._void_settlement_entry(original, actor=actor, reason=reason)
        if source == "loan_writeoff" and original.get("source_id"):
            return self._void_writeoff_entry(original, actor=actor, reason=reason)
        entry = accounting_repo.void_ledger_entry(
            tenant_id=self.tenant_id,
            entry_id=entry_id,
            actor=actor,
            reason=reason,
        )
        if not entry:
            raise RadiusNotFound("القيد غير موجود.")
        if source in _WALLET_SOURCES and (original.get("username") or original.get("subscriber_id")):
            amount = float(original.get("amount") or 0)
            delta = -amount if original.get("direction") == "credit" else amount
            self._shift_wallet(original, delta)
            entry["wallet_delta"] = round_money(delta)
            meta = json_load(original.get("metadata_json"), default={}) or {}
            try:
                minutes = int(meta.get("minutes") or 0)
            except (TypeError, ValueError):
                minutes = 0
            if source == "subscriber_time_extension" and minutes > 0:
                entry["time_reversal"] = revoke_activation_minutes(
                    username=str(original.get("username") or ""), minutes=minutes,
                    actor=actor, source=f"ledger_void:{entry_id}")
        return entry

    def _void_loan_entry(self, original: dict, *, actor: str, reason: str) -> dict:
        loan = self.get_loan(int(original["source_id"]))
        if accounting_repo.loan_posted_settlements(self.tenant_id, int(loan["id"])):
            raise RadiusConflict(
                "لهذه السلفة تسويات مُرحَّلة — اعكس قيود تسويتها أوّلًا ثم ألغِ السلفة.")
        if loan.get("status") != "open":
            raise RadiusConflict("السلفة ليست مفتوحة (مُسامَحة أو ملغاة مسبقًا).")
        entry = accounting_repo.void_ledger_entry(
            tenant_id=self.tenant_id, entry_id=int(original["id"]), actor=actor,
            reason=reason)
        if not accounting_repo.set_loan_status(self.tenant_id, int(loan["id"]),
                                               status="voided", expect=("open",)):
            raise RadiusConflict("السلفة ليست مفتوحة.")
        meta = json_load(loan.get("metadata_json"), default={}) or {}
        try:
            applied = int(meta.get("applied_minutes") or 0)
        except (TypeError, ValueError):
            applied = 0
        entry["loan"] = {"id": loan["id"], "status": "voided"}
        entry["time_reversal"] = (revoke_activation_minutes(
            username=str(loan.get("username") or ""), minutes=applied, actor=actor,
            source=f"loan_void:{loan['id']}") if applied > 0 else
            {"status": "skipped", "minutes": 0})
        return entry

    def _void_settlement_entry(self, original: dict, *, actor: str, reason: str) -> dict:
        settlement = accounting_repo.get_settlement(self.tenant_id, int(original["source_id"]))
        if not settlement or int(settlement.get("ledger_entry_id") or 0) != int(original["id"]):
            raise RadiusNotFound("التسوية غير موجودة.")
        loan = self.get_loan(int(settlement.get("loan_id") or 0))
        if loan.get("status") not in {"open", "settled"}:
            raise RadiusConflict(
                "السلفة مُسامَحة أو ملغاة — لا يمكن إعادة فتحها بعكس تسويتها.")
        out = accounting_repo.void_settlement(
            tenant_id=self.tenant_id, settlement_id=int(settlement["id"]), actor=actor,
            reason=reason)
        entry = accounting_repo.get_ledger_entry(
            self.tenant_id, self._reversal_id(int(original["id"]))) or {}
        entry["loan"] = {"id": out["loan_id"], "status": out["loan_status"],
                         "outstanding": out["loan_outstanding"]}
        return entry

    def _void_writeoff_entry(self, original: dict, *, actor: str, reason: str) -> dict:
        loan = self.get_loan(int(original["source_id"]))
        if loan.get("status") != "voided" or accounting_repo.ledger_entry_reversed(
                self.tenant_id, int(loan.get("ledger_entry_id") or 0)):
            raise RadiusConflict("لا توجد مسامحة قائمة لهذه السلفة لإلغائها.")
        entry = accounting_repo.void_ledger_entry(
            tenant_id=self.tenant_id, entry_id=int(original["id"]), actor=actor,
            reason=reason)
        accounting_repo.set_loan_status(self.tenant_id, int(loan["id"]), status="open",
                                        expect=("voided",))
        after = self.get_loan(int(loan["id"]))
        entry["loan"] = {"id": loan["id"], "status": after.get("status"),
                         "outstanding": after.get("outstanding", 0.0)}
        return entry

    def _reversal_id(self, entry_id: int) -> int:
        from ..db.connection import db as _db
        row = _db().execute(
            "SELECT id FROM accounting_ledger_entries WHERE tenant_id = ? "
            "AND reversal_of_entry_id = ? ORDER BY id DESC LIMIT 1",
            (self.tenant_id, int(entry_id))).fetchone()
        return int(row["id"]) if row else 0

    def get_payment(self, payment_id: int) -> dict:
        payment = accounting_repo.get_payment(self.tenant_id, payment_id)
        if not payment:
            raise RadiusNotFound("الدفعة غير موجودة.")
        return payment

    @atomic
    def void_payment(self, *, payment_id: int, actor: str, reason: str = "") -> dict:
        """إلغاء دفعة مرّة واحدة + **استرجاع الوقت الذي منحته**.

        الاسترجاع يطرح الدقائق المُطبَّقة فعلًا (``applied_minutes`` المحفوظة
        على الدفعة عند تطبيقها) من نهاية الاشتراك، دون النزول عن «الآن». دفعةٌ
        لم يُطبَّق وقتها (سجلّ فقط/معاينة) أو سابقة لهذا الحفظ ⇒ لا مساس بالوقت.

        موجة الإصلاح 2 (2026-09-29):
          • مشتركٌ كان **غير محدود** قبل الدفعة يعود غير محدود (كان انتهاؤه
            يُضبط «الآن» فيُقفل خارج الخدمة).
          • التسويات التي **سدّدتها هذه الدفعة** تُعكس أيضًا: كلّ تسوية سلفة
            (خيار «خصم») تُلغى وتعود السلفة مفتوحةً بمتبقّيها الصحيح، وكلّ
            «تسديد دين» من الرصيد السالب يُعكس فيعود الدين إلى الرصيد. مالٌ
            أُلغي لا يبقى يسدّد دينًا. يستطيع المالك تسويتها ثانيةً يدويًّا.
            المسامحات المختارة في نافذة الدفعة لا تُمسّ (قرار إعفاء لا مال).
            دفعاتٌ سُجِّلت قبل هذا التغيير لا تحمل رابط التسوية فلا تُعكس."""
        payment = self.get_payment(payment_id)
        if payment.get("status") == "voided":
            raise RadiusConflict("الدفعة مُلغاة مسبقًا.")
        result = accounting_repo.void_payment(
            tenant_id=self.tenant_id,
            payment=payment,
            actor=actor,
            reason=reason,
        )
        if not result:
            raise RadiusValidationError("قيد الدفعة في الدفتر غير موجود.")
        meta = json_load(payment.get("metadata_json"), default={}) or {}
        try:
            applied = int(meta.get("applied_minutes") or 0)
        except (TypeError, ValueError):
            applied = 0
        time_result = {"status": "skipped", "minutes": 0,
                       "reason": "لم يُطبَّق وقتٌ من هذه الدفعة على الحساب."}
        if applied > 0 and payment.get("username"):
            time_result = revoke_activation_minutes(
                username=str(payment["username"]), minutes=applied, actor=actor,
                source=f"payment_void:{payment['id']}",
                restore_unlimited=bool(meta.get("was_unlimited")),
            )
        result["time_reversal"] = time_result
        result["settlements_reversed"] = self._reverse_payment_settlements(
            payment, actor=actor, reason=reason)
        return result

    def _reverse_payment_settlements(self, payment: dict, *, actor: str,
                                     reason: str = "") -> dict:
        """عكسُ ما سدّدته الدفعة من سلفٍ ودين رصيد (داخل معاملة الإلغاء)."""
        note = reason or f"إلغاء الدفعة #{payment['id']}"
        loans: list[dict] = []
        for st in accounting_repo.payment_settlements(self.tenant_id, int(payment["id"])):
            loan = accounting_repo.get_loan(self.tenant_id, int(st.get("loan_id") or 0))
            if not loan or loan.get("status") not in {"open", "settled"}:
                continue  # سلفةٌ مُسامَحة/ملغاة بعد التسوية — تبقى كما هي
            loans.append(accounting_repo.void_settlement(
                tenant_id=self.tenant_id, settlement_id=int(st["id"]), actor=actor,
                reason=note))
        debt_total = 0.0
        for entry in accounting_repo.payment_debt_settlements(self.tenant_id,
                                                              int(payment["id"])):
            accounting_repo.void_ledger_entry(
                tenant_id=self.tenant_id, entry_id=int(entry["id"]), actor=actor,
                reason=note)
            self._shift_wallet(entry, -float(entry.get("amount") or 0))
            debt_total += float(entry.get("amount") or 0)
        return {"loans": loans, "debt_restored": round_money(debt_total)}

    def _shift_wallet(self, entry: dict, delta: float) -> None:
        """يضيف ``delta`` إلى رصيد مشترك القيد (سالبًا = يُنقص/يُعيد دينًا)."""
        from dataclasses import replace as _replace

        from ..core.errors import RadiusNotFound as _NotFound
        from ..integration.factory import get_radius_adapter
        username = str(entry.get("username") or "").strip()
        if not username and entry.get("subscriber_id"):
            sub = accounting_repo.resolve_subscriber(
                self.tenant_id, subscriber_id=int(entry["subscriber_id"]))
            username = str((sub or {}).get("username") or "")
        adapter = get_radius_adapter()
        try:
            account = adapter.get_account(username)
        except _NotFound:
            raise RadiusConflict(
                "المشترك صاحب القيد مؤرشف أو غير موجود — لا يمكن عكس أثره على الرصيد.") from None
        adapter.upsert_account(_replace(
            account, balance=round_money(float(account.balance or 0) + float(delta))))

    @atomic  # الدفعة + قيدها + تطبيق وقتها على الحساب: الكلّ أو لا شيء
    def create_payment(self, body: dict, *, actor: str,
                       distributor_id: int | None = None) -> dict:
        subscriber = self.resolve_subscriber(body)
        plan_id = body.get("plan_id") or subscriber.get("plan_id")
        # {"plan_id": "abc"} كان ValueError ⇒ 500.
        plan_id = _to_int(plan_id, field="plan_id", minimum=1) if plan_id else None
        plan = accounting_repo.resolve_plan(self.tenant_id, plan_id) if plan_id else None

        # المبلغ يُقرَّب لقرشين أوّلًا ثمّ يُفحص: 0.004 كان يمرّ «> 0» ويُسجَّل 0.00.
        amount = round_money(_to_float(body.get("amount"), field="amount", minimum=0))
        if amount < 0.01:
            raise NonFiniteNumber("المبلغ يجب أن يكون أكبر من صفر.", details={"field": "amount"})
        action_amount(amount, field="amount", kind="payment")
        # عملة الدفعة = عملة الرصيد/النظام ما لم تُرسَل عملةٌ مدعومة صراحةً — لا
        # عملة العرض: لا سعر صرف في النظام، والتطبيق والويب يرسلان عملة النظام.
        currency = normalize_currency(body.get("currency"))
        method = str(body.get("method") or "cash")[:40]
        notes = str(body.get("notes") or "")[:500]
        rounding = str(body.get("rounding_mode") or "floor")
        if rounding not in {"floor", "ceil", "nearest"}:
            raise RadiusValidationError("طريقة التقريب غير معروفة (floor أو ceil أو nearest).")

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
        self._check_discount(discount, plan_price)
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

        dry_run = _truthy(body.get("dry_run"))
        if dry_run:
            # «معاينة بدون تنفيذ»: لا دفعة ولا قيد ولا وقت ولا إشعار — فقط ما
            # كان سيحدث (كان: تُسجَّل دفعةٌ حقيقيّة ويُتخطّى تطبيق RADIUS فقط).
            activation_result = {"applied_to_radius": False, "dry_run": True,
                                 "source": "payment", "status": "planned"}
            if _truthy(body.get("apply_to_radius")) and earned_minutes > 0:
                activation_result = apply_activation_minutes(
                    username=subscriber["username"], minutes=earned_minutes,
                    actor=actor, source="payment:preview", dry_run=True)
            return {
                "id": None,
                "status": "preview",
                "subscriber_id": subscriber.get("id"),
                "username": subscriber.get("username"),
                "plan_id": plan.get("id") if plan else None,
                "amount": amount,
                "currency": currency,
                "method": method,
                "plan_price": default_price,
                "custom_price": custom_price_f,
                "discount_amount": discount,
                "effective_price": effective_price,
                "earned_minutes": earned_minutes,
                "rounding_mode": rounding,
                "notes": notes,
                "proportional_activation": {
                    "base_minutes": base_minutes,
                    "earned_minutes": earned_minutes,
                    "rounding_mode": rounding,
                    "applied_to_radius": False,
                },
                "activation_result": activation_result,
                "radius_action_id": None,
                "dry_run": True,
            }

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
            after_commit(lambda: notify_event(
                "payment_received", tenant_id=self.tenant_id,
                subscriber=_sub_obj, context={"amount": amount}))
        except Exception:  # noqa: BLE001
            pass
        # تنبيه إدارة باستلام دفعة (قناة الإدارة الموحّدة) — محصّن.
        _notify_admin_alert(self.tenant_id, "payment_received", {
            "username": subscriber.get("username") or "—",
            "amount": f"{float(amount):.2f} {currency}".strip(),
            "method": _payment_method_ar(method),
            "actor": actor,
        }, dedup_key=f"payment:{payment.get('id')}")
        apply_requested = _truthy(body.get("apply_to_radius"))
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
            if activation_result.get("applied_to_radius"):
                # يُحفظ ما طُبِّق فعلًا كي يسترجعه إلغاء الدفعة بالضبط.
                accounting_repo.mark_payment_applied(
                    self.tenant_id, int(payment["id"]), minutes=earned_minutes,
                    radius_action_id=str(activation_result.get("radius_action_id") or ""),
                    was_unlimited=activation_result.get("old_expire_at") is None)
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

    @staticmethod
    def _check_discount(discount: float, plan_price: float) -> None:
        """الخصم يُطرح من سعر الباقة، فلا يتجاوزه (كان خصم 50 على سعر 5 يُقبل
        201 بصمت ويُصفّر السعر). مشترك بين الـAPI والويب (create_payment)."""
        if discount > 0 and discount > float(plan_price or 0) + 0.005:
            raise RadiusValidationError(
                f"الخصم ({discount:,.2f}) أكبر من سعر الباقة ({float(plan_price or 0):,.2f}) — "
                "لا يمكن أن يتجاوز الخصم السعر.")

    def check_payment_discount(self, body: dict) -> None:
        """فحصٌ مبكّر للقراءة فقط (قبل أيّ بوّابة/أثر) — نفس قاعدة create_payment."""
        if body.get("discount_amount") in (None, "", 0, "0"):
            return
        subscriber = self.resolve_subscriber(body)
        plan_id = body.get("plan_id") or subscriber.get("plan_id")
        plan = accounting_repo.resolve_plan(self.tenant_id, int(plan_id)) if plan_id else None
        discount = _to_float(body.get("discount_amount") or 0, field="discount_amount", minimum=0)
        custom_price = body.get("custom_price")
        if custom_price not in (None, ""):
            plan_price = _to_float(custom_price, field="custom_price", minimum=0.01)
        else:
            plan_price = effective_subscriber_price(subscriber, plan)
        self._check_discount(discount, plan_price)

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

    @atomic  # السلفة + قيدها + نافذتها على الحساب: الكلّ أو لا شيء
    def create_loan(self, body: dict, *, actor: str) -> dict:
        subscriber = self.resolve_subscriber(body)
        amount, duration_minutes = self._loan_amount_and_minutes(body, subscriber)
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
            if body.get("duration_minutes") in (None, ""):
                # كان: «duration_minutes must be an integer» لسلفةٍ بلا أيام ولا ساعات.
                raise RadiusValidationError("حدّد مدّة السلفة (أيام و/أو ساعات).")
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
                from ..core import limits
                if duration_minutes > limits.max_extend_minutes():
                    raise NonFiniteNumber(limits.extend_too_long_msg(),
                                          details={"field": "duration_minutes"})
                raise RadiusValidationError(
                    f"مدة الدين تتجاوز الحدّ الأقصى المعقول ({cap_minutes // (24 * 60)} يومًا)."
                )
            raise RadiusValidationError(
                f"مدة السلفة المجانية تتجاوز الحدّ المسموح ({max_minutes // 60} ساعة) — "
                "للمُدد الأطول استخدم «تسجيل دين (مدين)»."
            )
        now = datetime.utcnow()
        loan_currency = normalize_currency(body.get("currency"))
        if _truthy(body.get("dry_run")):
            # المعاينة لا تكتب شيئًا: لا سلفة ولا قيد ولا تنبيه — فقط ما كان سيحدث.
            preview = {
                "id": None,
                "subscriber_id": subscriber.get("id"),
                "username": subscriber.get("username"),
                "duration_minutes": duration_minutes,
                "amount": amount,
                "currency": loan_currency,
                "status": "preview",
                "starts_at": dt_to_iso(now),
                "ends_at": dt_to_iso(now + timedelta(minutes=duration_minutes)),
            }
            result = {"applied_to_radius": False, "dry_run": True, "source": "loan",
                      "status": "planned"}
            if _truthy(body.get("apply_to_radius")):
                result = apply_activation_minutes(
                    username=subscriber["username"], minutes=duration_minutes,
                    actor=actor, source="loan:preview", dry_run=True,
                    respect_unlimited=True)
            preview["activation_window"] = {
                "starts_at": preview["starts_at"], "ends_at": preview["ends_at"],
                "duration_minutes": duration_minutes, "applied_to_radius": False,
            }
            preview["activation_result"] = result
            preview["radius_action_id"] = None
            preview["dry_run"] = True
            return preview
        loan = accounting_repo.create_loan(
            tenant_id=self.tenant_id,
            subscriber=subscriber,
            duration_minutes=duration_minutes,
            amount=amount,
            currency=loan_currency,
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
            # سلفة على مشتركٍ غير محدود (بلا تاريخ انتهاء) لا تفرض عليه نهاية.
            activation_result = apply_activation_minutes(
                username=subscriber["username"],
                minutes=duration_minutes,
                actor=actor,
                source=f"loan:{loan['id']}",
                dry_run=dry_run,
                respect_unlimited=True,
            )
            if activation_result.get("applied_to_radius"):
                # ما طُبِّق فعلًا — كي يسترجعه عكسُ قيد السلفة من الدفتر.
                accounting_repo.mark_loan_applied(self.tenant_id, int(loan["id"]),
                                                  minutes=duration_minutes)
        loan["activation_window"] = {
            "starts_at": loan["starts_at"],
            "ends_at": loan["ends_at"],
            "duration_minutes": duration_minutes,
            "applied_to_radius": bool(activation_result.get("applied_to_radius")),
        }
        loan["activation_result"] = activation_result
        loan["radius_action_id"] = activation_result.get("radius_action_id")
        loan["dry_run"] = dry_run
        # تنبيه إدارة بمنح سلفة وقت (قناة الإدارة الموحّدة) — محصّن.
        _loan_cur = loan_currency
        _notify_admin_alert(self.tenant_id, "loan_granted", {
            "username": subscriber.get("username") or "—",
            "duration": _fmt_minutes_ar(duration_minutes),
            "amount": (f"{float(amount):.2f} {_loan_cur}".strip()
                       if amount > 0 else "مجانية (بلا قيمة)"),
            "status": ("مُسجَّلة (دين)" if is_debt_loan else "سلفة مجانية"),
            "actor": actor,
            "reason": (str(body.get("reason") or "").strip() or "—"),
        }, dedup_key=f"loan:{loan.get('id')}")
        return loan

    def days_price(self, subscriber: dict, minutes: int) -> float:
        """Money value of ``minutes`` at the subscriber's effective price (offer /
        custom / manager price), 2 decimals — how a debt loan priced from its
        days (``price_from_days``) is valued. Shared by create_loan and the
        mobile API (so the manager gate sees the same amount that is recorded).

        الأساس الزمنيّ = ``price_basis`` (مدّة العرض، وإلّا شهر 30 يومًا) —
        الأساس نفسه الذي يسعّر به التمديد بالدين ومعاينة التطبيق والويب. كان
        عرضٌ بلا مدّة (12 ₪) يسجّل سلفة دين 0.00 بينما تعرض المعاينة 12.00."""
        basis = self.price_basis(subscriber)
        return calculate_proportional_amount(
            minutes=minutes,
            plan_price=basis["price"],
            base_minutes=basis["minutes"],
        )

    def _loan_amount_and_minutes(self, body: dict, subscriber: dict | None = None
                                 ) -> tuple[float, int]:
        """(قيمة السلفة، مدّتها بالدقائق) كما ستُسجَّل — **قبل** أيّ بوّابة.

        ``price_from_days`` يستبدل المبلغ المُرسَل بقيمة الأيام؛ فبوّابة
        الاعتماد وسقف سلف المدير يجب أن يَريا هذه القيمة لا ‎"amount": 0 المُرسَل
        (كان مديرٌ يتجاوز طابور الاعتماد وسقف الإنفاق بإرسال 0 مع price_from_days).
        """
        hours = body.get("hours")
        days = body.get("days")
        amount = _to_float(body.get("amount") or 0, field="amount", minimum=0)
        duration_minutes = 0
        if hours not in (None, ""):
            duration_minutes += _to_int(hours, field="hours", minimum=0) * 60
        if days not in (None, ""):
            duration_minutes += _to_int(days, field="days", minimum=0) * 24 * 60
        # F03 N11: an explicit ``duration_minutes`` is explicit time too — with an
        # amount it was ignored and the length derived from the price (60 min +
        # 5 ₪ became a 1-day loan). Given together with days/hours it must agree.
        raw_minutes = body.get("duration_minutes")
        if raw_minutes not in (None, "") and _to_int(raw_minutes, field="duration_minutes",
                                                   minimum=0) > 0:
            explicit = _to_int(raw_minutes, field="duration_minutes", minimum=1)
            if duration_minutes and duration_minutes != explicit:
                raise RadiusValidationError(
                    "حدّد مدّة السلفة مرّةً واحدة: الأيام/الساعات أو «duration_minutes» "
                    "(القيمتان المُرسَلتان مختلفتان).")
            duration_minutes = explicit
        # Operator-picks-DAYS flow: when the modal prices the loan from its
        # duration (price_from_days), derive the loan VALUE from the subscriber's
        # effective price (offer/custom), rounded to 2 decimals (operator choice).
        if _truthy(body.get("price_from_days")) and duration_minutes > 0:
            amount = self.days_price(subscriber or self.resolve_subscriber(body),
                                     duration_minutes)
        amount = round_money(amount)
        action_amount(amount, field="amount", kind="loan")
        return amount, duration_minutes

    def loan_amount(self, body: dict) -> float:
        """القيمة التي ستُسجَّل لسلفةٍ بهذا الجسم (لبوّابات المدير/الاعتماد)."""
        return self._loan_amount_and_minutes(body)[0]

    def _loan_range(self, date_from, date_to):
        """(lower, upper, exclusive) من يوم اللوحة المحلّيّ أو None — يرفع
        ReportDateError (رسالة عربيّة) لتاريخ غير صالح/نطاق مقلوب."""
        if not (str(date_from or "").strip() or str(date_to or "").strip()):
            return None
        from .report_dates import local_bounds
        return local_bounds(date_from, date_to, self.tenant_id)

    def list_loans(self, *, status: str = "", subscriber_id: int | None = None,
                   limit: int = 100, offset: int = 0,
                   date_from: str = "", date_to: str = "") -> list[dict]:
        return accounting_repo.list_loans(
            self.tenant_id,
            status=status,
            subscriber_id=subscriber_id,
            limit=limit,
            offset=offset,
            created_range=self._loan_range(date_from, date_to),
        )

    def get_loan(self, loan_id: int) -> dict:
        loan = accounting_repo.get_loan(self.tenant_id, loan_id)
        if not loan:
            raise RadiusNotFound("السلفة غير موجودة.")
        return loan

    def loan_totals(self, *, status: str = "", subscriber_id: int | None = None,
                    date_from: str = "", date_to: str = "") -> dict:
        return accounting_repo.loan_totals(self.tenant_id, status=status,
                                           subscriber_id=subscriber_id,
                                           created_range=self._loan_range(date_from, date_to))

    @atomic
    def settle_loan(self, loan_id: int, body: dict, *, actor: str,
                    subscriber_id: int | None = None) -> dict:
        """تسوية سلفة — كاملة أو **جزئيّة** (يبقى المتبقّي مفتوحًا).

        * بلا مبلغ / 0 ⇒ يُسدَّد المتبقّي كلّه.
        * مبلغ أكبر من المتبقّي ⇒ 422 (لا تسوية فوق الدين).
        * سلفة مجّانيّة (بلا قيمة) لا تقبل مالًا ⇒ 422؛ تُغلق بلا مبلغ.
        * ``subscriber_id`` (مسار «/users/<u>/loans/<id>») ⇒ السلفة يجب أن تخصّه.
        * العملة يجب أن تطابق عملة السلفة (لا تحويل عملات)."""
        loan = self.get_loan(loan_id)
        if subscriber_id is not None and int(loan.get("subscriber_id") or 0) != int(subscriber_id):
            raise RadiusNotFound("السلفة غير موجودة لهذا المشترك.")
        if loan["status"] != "open":
            raise RadiusConflict("السلفة ليست مفتوحة (مُسوّاة أو مُسامَحة مسبقًا).")
        outstanding = float(loan.get("outstanding") or 0)
        raw = body.get("amount")
        requested = 0.0 if raw in (None, "") else _to_float(raw, field="amount", minimum=0)
        if 0 < requested and round_money(requested) <= 0:
            # 0.004 كان يكتب قيد تسوية بقيمة 0.00 ويُبقي السلفة كما هي.
            raise NonFiniteNumber("مبلغ التسوية أقلّ من قرشٍ واحد (0.01).",
                                  details={"field": "amount"})
        requested = round_money(requested)
        loan_value = float(loan.get("amount") or 0)
        if loan_value <= 0 and requested > 0:
            raise RadiusValidationError(
                "هذه سلفة مجّانيّة (بلا قيمة ماليّة) — لا يُقبل مبلغ لتسويتها؛ "
                "أغلقها بلا مبلغ.")
        if requested <= 0:
            requested = outstanding
        if requested > outstanding + 0.005:
            raise RadiusValidationError(
                f"المبلغ ({requested:.2f}) يتجاوز المتبقّي على السلفة ({outstanding:.2f}).")
        loan_currency = normalize_currency(loan.get("currency"))
        currency = normalize_currency(body.get("currency"), default=loan_currency)
        if currency != loan_currency:
            raise RadiusValidationError(
                f"عملة التسوية ({currency}) يجب أن تطابق عملة السلفة ({loan_currency}).")
        settlement = accounting_repo.settle_loan(
            tenant_id=self.tenant_id,
            loan=loan,
            amount=round(requested, 2),
            currency=currency,
            method=str(body.get("method") or "manual")[:40],
            created_by=actor,
            notes=str(body.get("notes") or "")[:500],
            metadata={"settlement_type": str(body.get("settlement_type") or "manual")},
        )
        return settlement

    @atomic
    def writeoff_loan(self, loan_id: int, *, actor: str, notes: str = "") -> dict:
        loan = self.get_loan(loan_id)
        if loan["status"] != "open":
            raise RadiusConflict("السلفة ليست مفتوحة.")
        return accounting_repo.writeoff_loan(
            tenant_id=self.tenant_id,
            loan=loan,
            currency=normalize_currency(loan.get("currency")),
            created_by=actor,
            notes=(notes or "مسامحة سلفة")[:500],
        )

    def open_loans_for(self, *, subscriber_id: int) -> list[dict]:
        """Open loans for a subscriber, each annotated with its day-equivalent
        (duration_minutes / 1440) so the UI can show «٣ أيام / ٩ ₪».

        ``amount`` هنا = **المتبقّي** (بعد أيّ تسوية جزئيّة) لأنّه ما تُسوّيه
        الدفعة فعلًا؛ القيمة الأصليّة في ``original_amount``."""
        loans = accounting_repo.list_loans(
            self.tenant_id, status="open", subscriber_id=subscriber_id, limit=500,
        )
        for ln in loans:
            ln["days"] = round(int(ln.get("duration_minutes") or 0) / 1440.0, 2)
            ln["original_amount"] = float(ln.get("amount") or 0)
            ln["amount"] = float(ln.get("outstanding") or 0)
        return loans

    def plan_loan_actions(self, actions: list[dict], *, subscriber_id: int | None = None,
                          cash: float | None = None) -> list[dict]:
        """READ-ONLY: خيارات السلف من نافذة الدفعة/الرصيد → خطّة مُتحقَّق منها.

        * تكرار نفس السلفة بنفس الفعل يُدمج (لا خصم مزدوج)؛ بفعلين مختلفين ⇒ 422.
        * سلفةٌ لمشتركٍ آخر ⇒ 422 (``subscriber_id`` = صاحب الدفعة).
        * سلفة غير موجودة/مغلقة ⇒ تُتجاهل (كما كان).
        * «settle» تُسدِّد من ``cash`` (مبلغ الدفعة) بالترتيب: كلّ سلفةٍ بحدّ
          متبقّيها وبحدّ ما تبقّى من النقد — فدفعةٌ أصغر من السلفة تُسدّدها
          **جزئيًّا** وتبقى مفتوحةً بالباقي بدل إغلاقها كاملةً.
        Returns ``[{loan_id, action, amount}]`` (amount = المُسدَّد لكل settle)."""
        seen: dict[int, str] = {}
        plan: list[dict] = []
        remaining_cash = None if cash is None else max(float(cash), 0.0)
        for action in actions or []:
            loan_id = _loan_id_of(action)
            if loan_id is None:
                continue
            kind = str((action or {}).get("action") or "").strip()
            if loan_id in seen:
                if seen[loan_id] != kind:
                    raise RadiusValidationError(
                        f"السلفة #{loan_id} مكرّرة في الطلب بخيارين مختلفين.")
                continue
            seen[loan_id] = kind
            if kind not in {"settle", "writeoff"}:
                continue
            loan = accounting_repo.get_loan(self.tenant_id, loan_id)
            if not loan:
                continue
            if subscriber_id is not None and int(loan.get("subscriber_id") or 0) != int(subscriber_id):
                raise RadiusValidationError(f"السلفة #{loan_id} لا تخصّ هذا المشترك.")
            if loan.get("status") != "open":
                continue
            if kind == "writeoff":
                plan.append({"loan_id": loan_id, "action": "writeoff", "amount": 0.0})
                continue
            due = float(loan.get("outstanding") or 0)
            pay = due if remaining_cash is None else min(due, remaining_cash)
            pay = round(max(pay, 0.0), 2)
            if pay <= 0 and due > 0:
                continue  # لا نقد متبقٍّ لهذه السلفة ⇒ تبقى مفتوحة
            plan.append({"loan_id": loan_id, "action": "settle", "amount": pay})
            if remaining_cash is not None:
                remaining_cash = max(remaining_cash - pay, 0.0)
        return plan

    @atomic
    def resolve_loan_actions(self, actions: list[dict], *, actor: str,
                             subscriber_id: int | None = None,
                             cash: float | None = None,
                             payment_id: int | None = None) -> dict:
        """Apply per-loan operator choices from the payment/balance modal.

        Each action = {loan_id, action: 'settle'|'writeoff'} ('defer'/unknown =
        left open). Returns {settled_total, settled_ids, writeoff_ids} so the
        caller can DEDUCT settled_total from an incoming payment's time-basis.

        Items that already carry ``amount`` (a plan from ``plan_loan_actions``)
        settle exactly that amount; otherwise the plan is built here. Each loan
        is closed through a conditional update — a loan settled/forgiven in
        parallel is skipped, never settled twice.
        """
        if actions and all(isinstance(a, dict) and "amount" in a for a in actions):
            plan = actions
        else:
            plan = self.plan_loan_actions(actions, subscriber_id=subscriber_id, cash=cash)
        settled_total = 0.0
        settled_ids: list[int] = []
        writeoff_ids: list[int] = []
        for item in plan:
            loan_id = _loan_id_of(item)
            if loan_id is None:
                continue
            loan = accounting_repo.get_loan(self.tenant_id, loan_id)
            if not loan or loan.get("status") != "open":
                continue
            if subscriber_id is not None and int(loan.get("subscriber_id") or 0) != int(subscriber_id):
                continue
            currency = normalize_currency(loan.get("currency"))
            try:
                if item.get("action") == "settle":
                    amt = round(min(float(item.get("amount") or 0),
                                    float(loan.get("outstanding") or 0)), 2)
                    accounting_repo.settle_loan(
                        tenant_id=self.tenant_id, loan=loan, amount=amt,
                        currency=currency, method="payment", created_by=actor,
                        notes="تسوية مع دفعة", metadata={"settlement_type": "with_payment"},
                        payment_id=payment_id,
                    )
                    settled_total += amt
                    settled_ids.append(loan_id)
                elif item.get("action") == "writeoff":
                    accounting_repo.writeoff_loan(
                        tenant_id=self.tenant_id, loan=loan, currency=currency,
                        created_by=actor, notes="مسامحة سلفة",
                    )
                    writeoff_ids.append(loan_id)
            except RadiusConflict:
                continue  # أُغلقت بالتوازي — لا تسوية مزدوجة
        return {
            "settled_total": round(settled_total, 2),
            "settled_ids": settled_ids,
            "writeoff_ids": writeoff_ids,
        }

    def settle_preview_total(self, actions: list[dict], *,
                             subscriber_id: int | None = None,
                             cash: float | None = None) -> float:
        """READ-ONLY: what the chosen 'settle' loans will take — no mutation.

        Lets the payment route compute the time-basis deduction and create the
        payment FIRST; loans are only actually settled AFTER the payment succeeds,
        so a failed payment never leaves orphaned settlements. Duplicates are
        counted once, foreign loans are refused (422) and the total never
        exceeds ``cash`` (partial settlement of the last loan).
        """
        plan = self.plan_loan_actions(actions, subscriber_id=subscriber_id, cash=cash)
        return round(sum(float(i["amount"]) for i in plan if i["action"] == "settle"), 2)

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

    def subscriber_payments_page(self, *, limit: int | None = None,
                                 offset: int = 0) -> dict:
        """«دفعات المستفيدين» كاملة (أو صفحة) + إجماليّ SQL على الكلّ."""
        return {
            "items": accounting_repo.subscriber_payment_report(
                self.tenant_id, limit=limit, offset=offset),
            "totals": accounting_repo.subscriber_payment_totals(self.tenant_id),
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
        raise RadiusValidationError("نوع التقرير غير مدعوم.")

    # ── أعمدة كلّ تقرير (بالترتيب) — كي يَخرج التصدير برأسه حتى بلا بيانات،
    # وكي يعرض الويب والـAPI تسمياتٍ عربيّة لا مفاتيح خامّة. مفاتيح البيانات
    # نفسها لا تتغيّر (التطبيق يقرؤها)؛ التسمية فقط.
    REPORT_COLUMNS = {
        "daily": ("period", "transactions", "subscribers", "total", "avg_amount"),
        "monthly": ("period", "transactions", "subscribers", "total", "avg_amount"),
        "yearly": ("period", "transactions", "subscribers", "total", "avg_amount"),
        "subscriber_payments": ("subscriber_id", "username", "count", "total", "last_entry_at"),
        "loans": ("status", "count", "total", "outstanding", "duration_minutes"),
        "activations": ("username", "subscriber_id", "activation_count", "earned_minutes"),
        "card_sales": ("batch_id", "count", "total"),
        "profit_loss": ("credits", "debits", "net", "payments", "card_sales", "expenses",
                        "entries", "source"),
        "distributor_debts": ("distributor_id", "name", "display_name", "debt_balance",
                              "balance", "credit_limit"),
    }
    # أعمدة داخليّة لا تُعرض كعمود مستقلّ (تُستعمل لتنسيق المبالغ لكلّ عملة).
    _HIDDEN_COLUMNS = ("mixed_currency",)

    @classmethod
    def column_label(cls, column: str) -> str:
        return cls._PDF_COLUMN_LABELS.get(column, column)

    @classmethod
    def report_columns(cls, report_type: str, items: list[dict] | None = None) -> list[dict]:
        """``[{"key", "label"}]`` لأعمدة التقرير (الـAPI ``columns`` + رأس الويب)."""
        keys = list(cls.REPORT_COLUMNS.get(report_type, ()))
        for item in items or []:
            for key in item.keys():
                if key not in keys and key not in cls._HIDDEN_COLUMNS:
                    keys.append(key)
        return [{"key": k, "label": cls.column_label(k)} for k in keys]

    # قيمٌ خامّة تُعرَّب في العرض والتصدير (البيانات في JSON تبقى كما هي).
    _VALUE_LABELS = {
        "status": {"open": "مفتوحة", "settled": "مسدّدة", "voided": "ملغاة / مُسامَحة",
                   "posted": "مُرحَّلة", "pending": "معلّقة"},
        "source": {"accounting_ledger_entries": "دفتر القيود المحاسبيّة"},
    }

    @classmethod
    def value_label(cls, column: str, value: Any) -> Any:
        labels = cls._VALUE_LABELS.get(column)
        if labels and isinstance(value, str):
            return labels.get(value, value)
        return value

    def report_csv(self, *, report_type: str) -> str:
        items, columns = self._report_export_rows(report_type=report_type)
        out = io.StringIO()
        out.write("\ufeff")
        # الرأس يُكتب دائمًا — تقريرٌ بلا بيانات كان ملفًّا من 3 بايتات (BOM فقط).
        writer = csv.writer(out)
        writer.writerow([self.column_label(c) for c in columns])
        writer.writerows(
            [self._export_cell(column, item) for column in columns]
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
            ws.append([self.column_label(c) for c in columns])
        for item in items:
            ws.append([self._export_cell(column, item) for column in columns])
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
        "transactions": "عدد العمليات",
        "subscribers": "عدد المشتركين",
        "avg_amount": "متوسّط العملية",
        "outstanding": "المتبقّي",
        "by_currency": "حسب العملة",
        "duration_minutes": "الدقائق",
        "activation_count": "عدد التفعيلات",
        "earned_minutes": "الدقائق المكتسبة",
        "batch_id": "رقم الحزمة",
        "credits": "الإيرادات (دائن)",
        "debits": "المصروفات (مدين)",
        "payments": "دفعات محصَّلة (صافي الإلغاء)",
        "card_sales": "مبيعات الكروت",
        "expenses": "مصروفات الشركة",
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
        "total", "credits", "debits", "net", "outstanding", "avg_amount",
        "debt_balance", "balance", "credit_limit",
    }
    # الأعمدة العددية تُنسَّق بفواصل آلاف بدون كسور
    _PDF_COUNT_COLUMNS = {
        "count", "entries", "activation_count",
        "earned_minutes", "duration_minutes", "transactions", "subscribers",
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

            def _cell(column: str, item: dict) -> str:
                value = item.get(column)
                if value is None or value == "":
                    return "—"
                if column == "by_currency":
                    return str(self._export_cell(column, item))
                if column in self._PDF_MONEY_COLUMNS:
                    return fmt_money(value)
                if column in self._PDF_COUNT_COLUMNS:
                    return fmt_int(value)
                return str(self._export_cell(column, item))

            rows = [
                [_cell(column, item) for column in columns]
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
        # أعمدة التقرير الثابتة أوّلًا (فيبقى للتصدير الفارغ رأس) ثمّ أيّ مفتاح إضافيّ.
        columns = [c["key"] for c in self.report_columns(report_type, items)]
        return items, columns

    def _export_cell(self, column: str, item: dict) -> Any:
        """قيمة خليّة التصدير: ``by_currency`` نصًّا مقروءًا لكلّ عملة، والحالات/
        المصادر معرَّبة، وبقيّة القيم كما هي."""
        value = item.get(column)
        if column == "by_currency" and isinstance(value, list):
            key = next((k for k in ("total", "net", "outstanding") if value and k in value[0]),
                       "total")
            return " · ".join(
                f"{float(e.get(key) or 0):,.2f} {e.get('currency') or ''}".strip()
                for e in value if isinstance(e, dict))
        return self._export_value(self.value_label(column, value))

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
    def _snapshot_totals_by_currency(cls, items: list[dict]) -> list[dict]:
        """إجماليّ اللقطة لكلّ عملة (من ``by_currency`` كلّ صفّ) — لا جمع
        ILS+USD في رقمٍ واحد. قائمة فارغة للتقارير بلا تقسيم عملات."""
        column = next((c for c in cls._SNAPSHOT_TOTAL_COLUMNS if items and c in items[0]), None)
        if not column:
            return []
        sums: dict[str, float] = {}
        for row in items:
            for e in row.get("by_currency") or []:
                if isinstance(e, dict) and column in e:
                    cur = str(e.get("currency") or "")
                    sums[cur] = sums.get(cur, 0.0) + float(e.get(column) or 0)
        return [{"currency": c, "total": round(v, 2)} for c, v in sums.items()]

    @staticmethod
    def validate_report_range(date_from: Any, date_to: Any) -> tuple[str, str]:
        """نطاق لقطة/تقرير اختياريّ بصيغة YYYY-MM-DD ومرتّب — رسائل عربيّة.
        (الـAPI كان يقبل «garbage» ونطاقًا مقلوبًا فيحفظ لقطةً فارغة 201.)"""
        from datetime import date as _date

        out = []
        for value in (date_from, date_to):
            if value in (None, ""):
                out.append("")
                continue
            if not isinstance(value, str):
                raise RadiusValidationError("صيغة التاريخ غير صحيحة — استخدم سنة-شهر-يوم.")
            raw = value.strip()
            try:
                _date.fromisoformat(raw)
            except ValueError:
                raise RadiusValidationError("صيغة التاريخ غير صحيحة — استخدم سنة-شهر-يوم.")
            if len(raw) != 10:
                raise RadiusValidationError("صيغة التاريخ غير صحيحة — استخدم سنة-شهر-يوم.")
            out.append(raw)
        if out[0] and out[1] and out[0] > out[1]:
            raise RadiusValidationError("تاريخ «من» يجب أن يسبق تاريخ «إلى».")
        return out[0], out[1]

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
            # الإجماليّ لكلّ عملة (``total`` أعلاه مجموعٌ خامّ للتوافق).
            "totals_by_currency": self._snapshot_totals_by_currency(items),
            "range_applied": range_applied,
        }
        payload["mixed_currency"] = len(payload["totals_by_currency"]) > 1
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
            raise RadiusValidationError("اللقطة غير موجودة.")
        return snapshot


def service_from_context() -> AccountingService:
    from flask import g
    return AccountingService(int(getattr(g, "tenant_id", 1)))
