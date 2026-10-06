"""Unit + time normalisation for operations-assistant proposals.

* durations → minutes; ``months`` are CALENDAR months in the tenant's local
  zone (Asia/Gaza by default, DST-aware) — SPEC_DATA_v1 default for Q1;
* local wall-clock ``YYYY-MM-DDTHH:MM`` (``*_local``) → naive UTC;
* owner cap: ONE operation adds at most min(365 days, server
  ``limits.max_extend_days``) — repetition is allowed;
* speeds are kbps (1 Mbps = 1024 kbps), quotas MB (1 GB = 1024 MB) — the same
  constants as ``radius/core/units.py``;
* Arabic-Indic / Persian digits → Latin.
"""
from __future__ import annotations
from app.i18n_text import _tr

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Optional

KBPS_PER_MBPS = 1024
MB_PER_GB = 1024
OWNER_CAP_DAYS = 365                     # owner rule: max one year per operation
TEMP_SPEED_MIN_MINUTES = 1
TEMP_SPEED_MAX_MINUTES = 1440
TEMP_SPEED_MIN_KBPS = 64
TEMP_SPEED_MAX_KBPS = 1_000_000

_UNIT_MINUTES = {"minutes": 1, "hours": 60, "days": 1440}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class UnitError(ValueError):
    """A value cannot be normalised (message is Arabic, safe to show)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def latin_digits(text: str) -> str:
    return str(text or "").translate(_DIGITS)


def cap_minutes(tenant_id: Optional[int] = None) -> int:
    """min(owner cap 1 year, server «أقصى أيام في المرّة»)."""
    try:
        from ...core import limits
        server = int(limits.max_extend_minutes(tenant_id))
    except Exception:  # noqa: BLE001 — never widen the cap on an error
        server = OWNER_CAP_DAYS * 1440
    return min(OWNER_CAP_DAYS * 1440, server)


def tzinfo_for(tenant_id: int):
    from ...core.system_config import tenant_tzinfo
    return tenant_tzinfo(int(tenant_id))


def tz_name(tenant_id: int) -> str:
    try:
        from ...db.repos import tenants_repo
        return str(tenants_repo.get_setting(int(tenant_id), "billing.timezone", "Asia/Gaza")
                   or "Asia/Gaza")
    except Exception:  # noqa: BLE001
        return "Asia/Gaza"


def utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def local_today(tenant_id: int, now_utc: Optional[datetime] = None) -> date:
    now = (now_utc or utcnow()).replace(tzinfo=timezone.utc)
    return now.astimezone(tzinfo_for(tenant_id)).date()


def to_local(dt_utc: datetime, tenant_id: int) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(tzinfo_for(tenant_id)).replace(tzinfo=None)


def local_to_utc(text: str, tenant_id: int) -> datetime:
    """``YYYY-MM-DDTHH:MM`` in the tenant zone → naive UTC."""
    try:
        naive = datetime.strptime(latin_digits(text).strip(), "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError) as exc:
        raise UnitError("bad_datetime", _tr("صيغة الوقت المحلّي غير صحيحة (YYYY-MM-DDTHH:MM).")) from exc
    aware = naive.replace(tzinfo=tzinfo_for(tenant_id))
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def add_calendar_months(dt_utc: datetime, months: int, tenant_id: int) -> datetime:
    """Add calendar months on the LOCAL wall clock (day clamped to the month
    end), then back to UTC — DST-safe."""
    local = to_local(dt_utc, tenant_id)
    idx = local.month - 1 + int(months)
    year, month = local.year + idx // 12, idx % 12 + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    moved = local.replace(year=year, month=month, day=day)
    aware = moved.replace(tzinfo=tzinfo_for(tenant_id))
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def duration_minutes(duration: dict, *, tenant_id: int,
                     anchor_utc: Optional[datetime] = None) -> int:
    """``{"value": n, "unit": ...}`` → minutes. Months are calendar months
    from ``anchor_utc`` (default now) in the tenant zone."""
    value = int(duration["value"])
    unit = str(duration["unit"])
    if unit in _UNIT_MINUTES:
        return value * _UNIT_MINUTES[unit]
    if unit == "months":
        anchor = anchor_utc or utcnow()
        end = add_calendar_months(anchor, value, tenant_id)
        return int(round((end - anchor).total_seconds() / 60))
    raise UnitError("bad_unit", _tr("وحدة المدّة غير معروفة."))


def enforce_cap(minutes: int, tenant_id: int) -> int:
    cap = cap_minutes(tenant_id)
    if minutes <= 0:
        raise UnitError("non_positive_duration", _tr("المدّة يجب أن تكون أكبر من صفر."))
    if minutes > cap:
        raise UnitError(
            "over_one_year",
            _tr("أقصى إضافة في العمليّة الواحدة سنة (%(d)s يومًا) — قسّمها على أكثر من عمليّة.",
                d=cap // 1440))
    return minutes


def temp_speed_kbps(value: int) -> int:
    v = int(value)
    if v == 0:
        return 0
    if v < TEMP_SPEED_MIN_KBPS or v > TEMP_SPEED_MAX_KBPS:
        raise UnitError("speed_range", _tr("كل اتجاه للسرعة المؤقتة 0 (بلا حدّ) أو بين 64 و1,000,000 kbps."))
    return v


def temp_speed_minutes(minutes: int) -> int:
    if not TEMP_SPEED_MIN_MINUTES <= int(minutes) <= TEMP_SPEED_MAX_MINUTES:
        raise UnitError("temp_speed_window", _tr("مدّة السرعة المؤقتة بين دقيقة و1440 دقيقة (24 ساعة)."))
    return int(minutes)


def format_speed(kbps: int) -> str:
    """2048 → '2 Mbps', 1536 → '1.5 Mbps', 512 → '512 kbps', 0 → 'unlimited'."""
    k = int(kbps or 0)
    if k <= 0:
        return "unlimited"
    if k >= KBPS_PER_MBPS * KBPS_PER_MBPS:
        return f"{k / (KBPS_PER_MBPS * KBPS_PER_MBPS):g} Gbps"
    if k >= KBPS_PER_MBPS:
        return f"{k / KBPS_PER_MBPS:g} Mbps"
    return f"{k} kbps"


def format_quota(mb: int) -> str:
    m = int(mb or 0)
    if m >= MB_PER_GB:
        return f"{m / MB_PER_GB:g} GB"
    return f"{m} MB"


def mbps_to_kbps(mbps: float) -> int:
    return int(round(float(mbps) * KBPS_PER_MBPS))


def gb_to_mb(gb: float) -> int:
    return int(round(float(gb) * MB_PER_GB))


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"


def local_text(dt_utc: Optional[datetime], tenant_id: int) -> str:
    if dt_utc is None:
        return ""
    return to_local(dt_utc, tenant_id).strftime("%Y-%m-%d %H:%M")


def minutes_text(minutes: int) -> str:
    m = int(minutes)
    if m % 1440 == 0:
        return f"{m // 1440} d"
    if m % 60 == 0:
        return f"{m // 60} h"
    return f"{m} min"


__all__ = [
    "KBPS_PER_MBPS", "MB_PER_GB", "OWNER_CAP_DAYS", "UnitError", "latin_digits",
    "cap_minutes", "local_today", "local_to_utc", "add_calendar_months",
    "duration_minutes", "enforce_cap", "temp_speed_kbps", "temp_speed_minutes",
    "format_speed", "format_quota", "mbps_to_kbps", "gb_to_mb", "iso_z",
    "local_text", "minutes_text", "utcnow", "tz_name", "to_local",
]
