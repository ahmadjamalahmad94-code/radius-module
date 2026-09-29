"""تحليل الأرقام الماليّة/العدديّة من المدخلات — مصدرٌ واحد لكل المسارات.

🔴 لماذا؟ ``float("inf")`` و``float("nan")`` و``float("1e400")`` كلّها «أرقام»
في بايثون. كانت المسارات تقبلها في الرصيد والمبالغ والأسعار وحدّ الائتمان
والكوتة، فظهر:
  • رصيد ‎-Infinity يكسر JSON كل قائمة تقرؤه (التطبيق لا يفتح القائمة).
  • NaN يُخزَّن NULL بصمت: رصيد المشترك صار 0 (مُسح دينٌ ‎-62,831) ثم فشل
    قيد الدفتر NOT NULL بعد أن حُفظ المشترك.
  • ‎NaN < 0 خطأ و‎NaN > 0 خطأ — فكل فحص «المبلغ موجب» يمرّره.

``finite_float`` ترفض كل ذلك برسالة عربية. ``NonFiniteNumber`` ترث من
``RadiusValidationError`` **و**``ValueError`` معًا، فتلتقطها كتل
``except (TypeError, ValueError)`` الموجودة في المسارات كما تلتقطها كتل
``except RadiusError`` — بلا تغيير في هيكل المعالجة.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from .errors import RadiusValidationError

# سقفٌ عاقل لأيّ مبلغ واحد (يكفي أكبر العملات المحليّة ويمنع 1e308).
MONEY_MAX = 1_000_000_000.0

# ── سقوف العمليّة الواحدة (موجة الإصلاح 2 + قرار المالك 2026-09-29) ──
# • مبلغ دفعة/تمديد/سلفة/رصيد/كوتة/حركة موزّع واحدة ≤ 100,000 بعملة النظام.
# • **أقصى تمديد في المرّة الواحدة سنة (365 يومًا)**: new_expiry − max(now,
#   current_expiry) ≤ 365 يومًا — للتمديد بمدّة، ولقفزة تعيين التاريخ، ولدقائق
#   الدفعة المحوَّلة وقتًا، ولوقت السلفة، وللتمديد الجماعيّ. التكرار مسموح.
# • أيّ انتهاءٍ محسوب بعد سنة 2100 ⇒ 422. كان 1e6 يُنتج سنة 3200/3669 و5e6–1e9
#   يُسقط الخادم بـ500 «date value out of range».
ACTION_AMOUNT_MAX = 100_000.0
EXTEND_MAX_DAYS = 365
EXTEND_MAX_MINUTES = EXTEND_MAX_DAYS * 1440
EXTEND_TOO_LONG_AR = "أقصى تمديد في المرة الواحدة سنة — كرّر التمديد إن احتجت أكثر."
EXPIRY_LIMIT = datetime(2101, 1, 1)
EXPIRY_TOO_FAR_AR = "المدة الناتجة تتجاوز الحدّ المسموح."

_FIELD_AR = {
    "amount": "المبلغ",
    "balance": "الرصيد",
    "price": "السعر",
    "custom_price": "السعر الخاص",
    "discount_amount": "الخصم",
    "credit_limit": "حدّ الائتمان",
    "quota_mb": "الكوتة",
    "minutes": "المدّة",
    "hours": "الساعات",
    "days": "الأيام",
    "loan_settled_total": "مجموع تسوية السلف",
    "balance_settled_total": "مجموع تسوية الدين",
    "duration_minutes": "المدّة بالدقائق",
    "plan_id": "العرض",
}

_MISSING = object()

# 🔴 أرقام لوحة المفاتيح العربيّة/الفارسيّة (R12 N1): الفاصلة العشريّة «٫»
# (U+066B) كانت تُحذف في الواجهة فيصير ٣٥٫٥ ⇒ 355، وهنا كان ``float("٣٥٫٥")``
# يفشل. ``float``/``int`` في بايثون تقرأ الأرقام العربيّة-الهنديّة والفارسيّة
# أصلًا؛ ينقصها الفواصل: «٫» ⇒ «.»، فاصل الآلاف «٬» (U+066C) ⇒ يُحذف، الناقص
# الطباعيّ «−» ⇒ «-»، ومحارف الاتّجاه الخفيّة (LRM/RLM/ALM) والمسافة غير
# الفاصلة ⇒ تُحذف. الفاصلة «,»/«،» لا تُحوَّل هنا عمدًا: «1,000» ملتبسة
# (ألف؟ واحد؟) فنرفضها برسالة بدل تغيير المقدار بصمت.
_NUM_TRANS = {0x066B: ".", 0x066C: None, 0x2212: "-", 0xFE63: "-", 0xFF0D: "-",
              0x200E: None, 0x200F: None, 0x061C: None, 0x00A0: None, 0x202F: None}
for _i in range(10):
    _NUM_TRANS[0x0660 + _i] = str(_i)   # ٠-٩
    _NUM_TRANS[0x06F0 + _i] = str(_i)   # ۰-۹
del _i


def normalize_number_text(value: Any) -> Any:
    """نصّ رقميّ من لوحة عربيّة/فارسيّة ⇒ صيغة لاتينيّة يفهمها ``float``.

    غير النصوص تعود كما هي. «٣٥٫٥» ⇒ «35.5»، «١٬٠٠٠» ⇒ «1000»، «−٥» ⇒ «-5»."""
    if not isinstance(value, str):
        return value
    return value.translate(_NUM_TRANS).strip()


# نصٌّ «رقميّ صِرف» مكتوبٌ بمحارف عربيّة/فارسيّة: إشارة اختياريّة، أرقام
# (مع «٬» للآلاف)، ثم كسرٌ اختياريّ بعد «٫»/«.». لا يطابق أيّ نصٍّ فيه حرفٌ آخر.
_AR_NUMERIC_RE = re.compile(
    r"^\s*[-−﹣－]?[‎‏؜]*"
    r"[0-9٠-٩۰-۹٬]*"
    r"(?:[.٫][0-9٠-٩۰-۹]*)?[‎‏؜]*\s*$")
_AR_NUMERIC_MARK = re.compile(r"[٠-٩۰-۹٫٬−﹣－]")
_DIGIT_ANY = re.compile(r"[0-9٠-٩۰-۹]")


def normalize_numeric_text(value: Any) -> Any:
    """``normalize_number_text`` لكن **فقط** لنصٍّ رقميّ صِرف فيه محرفٌ عربيّ
    (رقم ٠-٩/۰-۹ أو «٫»/«٬»/«−»). أيّ نصٍّ آخر (اسم، ملاحظة، «1,5») يعود كما
    هو حرفيًّا — آمنٌ لتطبيقه على كل حقول نموذج الويب."""
    if (not isinstance(value, str) or not _AR_NUMERIC_MARK.search(value)
            or not _DIGIT_ANY.search(value) or not _AR_NUMERIC_RE.match(value)):
        return value
    return normalize_number_text(value)


class NonFiniteNumber(RadiusValidationError, ValueError):
    """رقمٌ غير صالح (غير منتهٍ/خارج الحدود/ليس رقمًا). 422 في الـ API."""

    code = "validation_error"
    http_status = 422


def field_label(field: str) -> str:
    return _FIELD_AR.get(field, field)


def finite_float(value: Any, *, field: str = "amount", min: Optional[float] = None,
                 max: Optional[float] = None, default: Any = _MISSING) -> float:
    """``float(value)`` مع رفض Infinity/NaN/الفائض والنصوص غير الرقمية.

    ``None``/``""`` → ``default`` إن أُعطي، وإلا خطأ. ``True/False`` مرفوضة
    (bool رقمٌ في بايثون لكنه ليس مبلغًا). ``min``/``max`` حدود شاملة."""
    label = field_label(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not _MISSING:
            return default
        raise NonFiniteNumber(f"قيمة «{label}» مطلوبة.", details={"field": field})
    if isinstance(value, bool):
        raise NonFiniteNumber(f"قيمة «{label}» يجب أن تكون رقمًا.", details={"field": field})
    try:
        out = float(normalize_number_text(value))
    except (TypeError, ValueError, OverflowError):
        raise NonFiniteNumber(f"قيمة «{label}» يجب أن تكون رقمًا.",
                              details={"field": field}) from None
    if not math.isfinite(out):
        raise NonFiniteNumber(f"قيمة «{label}» يجب أن تكون رقمًا منتهيًا صالحًا.",
                              details={"field": field})
    if min is not None and out < min:
        raise NonFiniteNumber(f"قيمة «{label}» يجب ألّا تقلّ عن {_fmt(min)}.",
                              details={"field": field})
    if max is not None and out > max:
        raise NonFiniteNumber(f"قيمة «{label}» يجب ألّا تزيد عن {_fmt(max)}.",
                              details={"field": field})
    return out


def strict_float(value: Any, field: str = "") -> float:
    """بديلٌ مباشر لـ ``float(value)`` في نقاط تحليل المدخلات: السلوك نفسه
    (``ValueError``/``TypeError`` للنصّ غير الرقميّ) لكن Infinity/NaN/الفائض
    ترمي ``NonFiniteNumber`` (وهي ``ValueError`` أيضًا) — فكتل
    ``except (TypeError, ValueError)`` الموجودة تعالجها كقيمةٍ خاطئة."""
    out = float(normalize_number_text(value))
    if not math.isfinite(out) or abs(out) > MONEY_MAX * 1000:
        label = field_label(field) if field else "الرقم"
        raise NonFiniteNumber(f"قيمة «{label}» يجب أن تكون رقمًا منتهيًا صالحًا.",
                              details={"field": field} if field else None)
    return out


def money_float(value: Any, *, field: str = "amount", min: Optional[float] = None,
                max: Optional[float] = MONEY_MAX, default: Any = _MISSING) -> float:
    """مبلغ: ``finite_float`` بسقف ``MONEY_MAX`` ومقرّبًا لخانتين."""
    out = finite_float(value, field=field, min=min, max=max, default=default)
    if isinstance(out, float):
        return round_money(out)
    return out


def finite_int(value: Any, *, field: str, min: Optional[int] = None,
               max: Optional[int] = None, default: Any = _MISSING) -> int:
    """عدد صحيح من مُدخل (يقبل "5" و5.0) مع رفض الكسور وغير المنتهي."""
    out = finite_float(value, field=field, min=min, max=max, default=default)
    if not isinstance(out, float):
        return out
    if not out.is_integer():
        raise NonFiniteNumber(f"قيمة «{field_label(field)}» يجب أن تكون عددًا صحيحًا.",
                              details={"field": field})
    return int(out)


def round_money(value: Any) -> float:
    """تقريب المال لخانتين عند الكتابة (يزيل ضجيج ‎-42.89999999999999 و‎-0.0).

    قاعدةٌ واحدة في كلّ مكان: **نصف للأعلى** على القيمة العشريّة كما تُكتب
    (``repr``). كان ``round()`` الثنائيّ يعطي 0.625 → 0.62 و376.905 → 376.90
    بينما يعطي الويب والتطبيق 0.63 — فاختلف السعر بين المسارات."""
    f = float(value or 0)
    if not math.isfinite(f):
        return f
    out = float(Decimal(repr(f)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return 0.0 if out == 0 else out


def money_cents(value: Any) -> int:
    """المبلغ بالقروش (عددٌ صحيح) بعد ``round_money``."""
    return int((Decimal(repr(round_money(value))) * 100).to_integral_value())


def action_amount(value: Any, *, field: str = "amount") -> Any:
    """422 لمبلغ عمليّةٍ واحدة فوق ``ACTION_AMOUNT_MAX`` (دفعة/تمديد/سلفة/رصيد…)."""
    if value is not None and float(value) > ACTION_AMOUNT_MAX + 1e-9:
        raise NonFiniteNumber(
            f"قيمة «{field_label(field)}» تتجاوز الحدّ الأقصى للعملية الواحدة "
            f"({_fmt(ACTION_AMOUNT_MAX)}).", details={"field": field})
    return value


def check_expiry(dt: Optional[datetime]) -> Optional[datetime]:
    """422 «المدة الناتجة تتجاوز الحدّ المسموح» لانتهاءٍ محسوب بعد سنة 2100."""
    if dt is not None and dt >= EXPIRY_LIMIT:
        raise NonFiniteNumber(EXPIRY_TOO_FAR_AR, details={"field": "expire_at"})
    return dt


def add_minutes_capped(base: datetime, minutes: int) -> datetime:
    """``base + minutes`` مع حارس السقف: الفائض (OverflowError = 500 سابقًا) وما
    بعد سنة 2100 ⇒ 422 بالرسالة نفسها."""
    try:
        out = base + timedelta(minutes=int(minutes))
    except (OverflowError, ValueError):
        raise NonFiniteNumber(EXPIRY_TOO_FAR_AR, details={"field": "expire_at"}) from None
    return check_expiry(out)


def check_extend_minutes(minutes: int) -> int:
    """422 «أقصى تمديد في المرة الواحدة سنة…» لإضافةٍ فوق 365 يومًا في عمليّة
    واحدة (كان 999,999 يومًا يُقبل من الويب). التكرار مسموح."""
    if int(minutes) > EXTEND_MAX_MINUTES:
        raise NonFiniteNumber(EXTEND_TOO_LONG_AR, details={"field": "minutes"})
    return int(minutes)


def json_safe(obj: Any) -> Any:
    """نسخة من ``obj`` تستبدل كل float غير منتهٍ بـ ``None`` (JSON صالح)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def json_dumps_safe(obj: Any, dumps, **kwargs) -> str:
    """``dumps(obj, allow_nan=False)``؛ وعند وجود Infinity/NaN في أيّ مكان
    (حتى داخل كائنات يحوّلها ``default``) نحلّل الناتج ونستبدلها بـ null —
    صفٌّ واحد فاسد لا يُسقط قائمةً كاملة."""
    kwargs["allow_nan"] = False
    try:
        return dumps(obj, **kwargs)
    except ValueError as exc:
        if "out of range float" not in str(exc).lower():
            raise
    kwargs["allow_nan"] = True
    loose = dumps(obj, **kwargs)
    kwargs["allow_nan"] = False
    for key in ("default", "cls"):
        kwargs.pop(key, None)
    return dumps(json_safe(json.loads(loose)), **kwargs)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


__all__ = [
    "ACTION_AMOUNT_MAX", "EXPIRY_LIMIT", "EXPIRY_TOO_FAR_AR", "EXTEND_MAX_DAYS",
    "EXTEND_MAX_MINUTES", "EXTEND_TOO_LONG_AR", "MONEY_MAX", "NonFiniteNumber", "action_amount",
    "add_minutes_capped", "check_expiry", "check_extend_minutes", "field_label",
    "finite_float", "finite_int", "json_dumps_safe", "json_safe", "money_cents",
    "money_float", "normalize_number_text", "normalize_numeric_text", "round_money",
    "strict_float",
]
