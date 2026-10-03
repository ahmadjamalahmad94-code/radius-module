"""فلاتر تاريخ التقارير — يوم المشغّل المحلّيّ، شاملًا، على طوابع مطبَّعة.

مصدرٌ واحد لصفحات تقارير الويب (/reports/…) ولواجهة ``/api/v1/operational-reports``:

* ``YYYY-MM-DD`` = **يوم محلّيّ كامل** (منطقة اللوحة): ``from`` = بدايته،
  ``to`` = بداية اليوم التالي (حدّ حصريّ) — فـ from=to=اليوم يعيد صفوف اليوم
  كلّها. كانت المقارنة ``created_at <= 'YYYY-MM-DD 23:59:59'`` نصّيّةً على يوم
  UTC: صفّ ‎2026-09-29T10:00Z‎ «أكبر» من الحدّ (‏'T' > ' ') فتُعيد 0 صفوف،
  و``to`` يقطع عند منتصف ليل UTC (الثالثة فجرًا محلّيًّا).
* قيمة بوقت: بلاحقة Z/إزاحة = لحظة مطلقة؛ بلا لاحقة = ساعة محلّيّة (شاملة).
* المقارنة على ``datetime(col)`` (يطبّع ‎…T…Z‎ و«المسافة» معًا) مع مرشّحٍ
  خشنٍ على بادئة التاريخ الخامّ كي يبقى فهرس (tenant_id, created_at) صالحًا.

تاريخٌ غير صالح أو نطاقٌ مقلوب → :class:`ReportDateError` برسالة عربيّة
(الـAPI: 422؛ الويب: تنبيه + عرض بلا فلترة).
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

from datetime import date, timedelta
from typing import Any


class ReportDateError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_BAD_DATE = N_("صيغة التاريخ غير صحيحة — استخدم YYYY-MM-DD.")
_BAD_ORDER = N_("تاريخ «من» يجب أن يسبق تاريخ «إلى».")


def _bound(value: Any, *, end: bool, tenant_id: int | None) -> str | None:
    from .events_risk_center import EventsRiskError, _date_bound

    if value is not None and not isinstance(value, str):
        raise ReportDateError(_BAD_DATE)
    try:
        return _date_bound(value, end=end, tenant_id=tenant_id)
    except EventsRiskError as exc:
        raise ReportDateError(str(exc) or _BAD_DATE) from exc
    except (TypeError, ValueError) as exc:
        raise ReportDateError(_BAD_DATE) from exc


def local_bounds(date_from: Any, date_to: Any,
                 tenant_id: int | None = None) -> tuple[str | None, str | None, bool]:
    """(حدّ أدنى UTC، حدّ أعلى UTC، هل الأعلى حصريّ) — أو ReportDateError."""
    lower = _bound(date_from, end=False, tenant_id=tenant_id)
    upper = _bound(date_to, end=True, tenant_id=tenant_id)
    upper_exclusive = len(str(date_to or "").strip()) == 10
    if lower and upper:
        if (upper <= lower) if upper_exclusive else (upper < lower):
            raise ReportDateError(_BAD_ORDER)
    return lower, upper, upper_exclusive


def _day_prefix(ts: str, *, days: int = 0) -> str:
    d = date.fromisoformat(ts[:10]) + timedelta(days=days)
    return d.isoformat()


def range_sql(column: str, lower: str | None, upper: str | None,
              upper_exclusive: bool = True) -> tuple[list[str], list[Any]]:
    """جمل WHERE + معاملاتها لنطاق [lower, upper) على عمود طابع زمنيّ خامّ
    (أو تعبير مطبَّع مثل ``acct_norm_sql``)."""
    where: list[str] = []
    params: list[Any] = []
    if lower:
        # خشن (يستعمل الفهرس): بادئة يوم UTC للحدّ — أيّ صيغة تبدأ بالتاريخ.
        where.append(f"{column} >= ?")
        params.append(_day_prefix(lower))
        where.append(f"datetime({column}) >= datetime(?)")
        params.append(lower)
    if upper:
        where.append(f"{column} < ?")
        params.append(_day_prefix(upper, days=1))
        op = "<" if upper_exclusive else "<="
        where.append(f"datetime({column}) {op} datetime(?)")
        params.append(upper)
    return where, params


def date_range_sql(column: str, date_from: Any, date_to: Any,
                   tenant_id: int | None = None) -> tuple[list[str], list[Any]]:
    """اختصار: تحقّق + جمل SQL. يرفع ReportDateError عند قيمة غير صالحة."""
    lower, upper, excl = local_bounds(date_from, date_to, tenant_id)
    return range_sql(column, lower, upper, excl)


def strict_int(value: Any, *, default: int, minimum: int, maximum: int,
               label: str) -> int:
    """``limit``/``offset`` صارمان: غير رقميّ → ReportDateError عربيّ (لا صمتٌ
    يحوّل ``limit=abc`` إلى 100)، والقيمة تُقصّ ضمن [minimum, maximum]."""
    if value in (None, ""):
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ReportDateError(_tr('قيمة %(label)s يجب أن تكون رقمًا صحيحًا.', label=label))
    return min(max(number, minimum), maximum)


__all__ = ["ReportDateError", "local_bounds", "range_sql", "date_range_sql", "strict_int"]
