"""تحليل تواريخ الـ API إلى UTC ساكن (naive) — مصدرٌ واحد.

🔴 القاعدة المتّفق عليها: كل تاريخ يُخزَّن **UTC ساكنًا** (بلا tzinfo).
كان ``accounts._parse_dt`` يحذف «Z» فقط، فتاريخٌ بإزاحة مثل
``2026-10-01T10:00:00+03:00`` يُخزَّن **واعيًا** (aware) — ثم تقارنه لوحة
التحكّم بـ ``datetime.utcnow()`` الساكن فتسقط بـ 500 للجميع
(«can't compare offset-naive and offset-aware datetimes»).

  • قيمة بـ ``Z`` أو إزاحة → تلك اللحظة محوّلةً إلى UTC ثم ساكنة.
  • قيمة ساكنة → تُعدّ UTC كما هي (سلوك الـ API الموثّق).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """aware → UTC ساكن؛ الساكن يبقى كما هو (يُعدّ UTC)."""
    if dt is None or not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is not None and dt.utcoffset() is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def parse_iso_utc(value: Any, *, strict: bool = False) -> Optional[datetime]:
    """ISO (بـ Z/إزاحة أو بدونها) → ``datetime`` UTC ساكن.

    ``None``/``""``/``0`` → ``None``. نصٌّ غير صالح → ``None``، أو
    ``ValueError`` إن ``strict``."""
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raw = str(value).strip()
    if not raw:
        return None
    # «…+03:00Z» (إزاحة ثم Z) كُتب سابقًا بخطأ في التخزين — نقبله.
    if raw.endswith("Z") and ("+" in raw[10:-1] or "-" in raw[10:-1]):
        raw = raw[:-1]
    elif raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        return to_naive_utc(datetime.fromisoformat(raw))
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"bad datetime: {value!r}") from None
        return None


def iso_utc_z(value: Any) -> Optional[str]:
    """``datetime``/نص → ISO UTC بلاحقة ``Z`` (صيغة خرج الـ API الموحّدة)."""
    dt = parse_iso_utc(value)
    if dt is None:
        return None if value in (None, "") else str(value)
    return dt.isoformat() + "Z"


__all__ = ["iso_utc_z", "parse_iso_utc", "to_naive_utc"]
