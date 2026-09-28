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
from typing import Any, Optional

from .errors import RadiusValidationError

# سقفٌ عاقل لأيّ مبلغ واحد (يكفي أكبر العملات المحليّة ويمنع 1e308).
MONEY_MAX = 1_000_000_000.0

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
}

_MISSING = object()


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
        out = float(value.strip() if isinstance(value, str) else value)
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
    out = float(value)
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
    """تقريب المال لخانتين عند الكتابة (يزيل ضجيج ‎-42.89999999999999 و‎-0.0)."""
    out = round(float(value or 0), 2)
    return 0.0 if out == 0 else out


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
    "MONEY_MAX", "NonFiniteNumber", "field_label", "finite_float", "finite_int",
    "json_dumps_safe", "json_safe", "money_float", "round_money", "strict_float",
]
