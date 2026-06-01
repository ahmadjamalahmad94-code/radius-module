"""Single source of truth for system-wide display config: currency, timezone,
system name, country, logo. Read from tenant_settings (editable from the
control panel). Exposed to all templates as `cfg`, plus the `money` and
`dt_local` Jinja filters so currency and time are unified everywhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from flask import g

CURRENCY_SYMBOLS = {
    "JOD": "د.أ", "ILS": "₪", "USD": "$", "IQD": "د.ع",
    "SAR": "ر.س", "EGP": "ج.م", "AED": "د.إ", "EUR": "€", "TRY": "₺",
}
CURRENCY_NAMES = {
    "JOD": "دينار أردني", "ILS": "شيكل", "USD": "دولار", "IQD": "دينار عراقي",
    "SAR": "ريال سعودي", "EGP": "جنيه مصري", "AED": "درهم", "EUR": "يورو", "TRY": "ليرة",
}

_DEFAULTS = {
    "billing.currency": "JOD",
    "billing.timezone_offset": "3",
    "system.name": "HobeRadius",
    "radius.default_country": "",
    "branding.logo_url": "",
    "branding.primary_color": "#2BAACC",
}


def _tid() -> int:
    from .tenant import DEFAULT_TENANT_ID
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _get(key: str) -> str:
    try:
        from ..db.repos import tenants_repo
        val = tenants_repo.get_setting(_tid(), key, _DEFAULTS.get(key, ""))
        return str(val or _DEFAULTS.get(key, "")).strip()
    except Exception:  # noqa: BLE001 — settings must never break a page
        return _DEFAULTS.get(key, "")


def system_config() -> dict[str, Any]:
    currency = (_get("billing.currency") or "JOD").upper()
    try:
        tz_offset = float(_get("billing.timezone_offset") or 3)
    except (TypeError, ValueError):
        tz_offset = 3.0
    return {
        "currency": currency,
        "currency_symbol": CURRENCY_SYMBOLS.get(currency, currency),
        "currency_name": CURRENCY_NAMES.get(currency, currency),
        "tz_offset": tz_offset,
        "system_name": _get("system.name") or "HobeRadius",
        "country": _get("radius.default_country"),
        "logo_url": _get("branding.logo_url"),
        "primary_color": _get("branding.primary_color") or "#2BAACC",
    }


def default_currency() -> str:
    """إرجاع رمز العملة المضبوطة للمستأجر.

    هذا هو المرجع الموحد لأي سجل أو نموذج جديد بدل تثبيت ``"JOD"`` داخل
    الكود. يقرأ ``billing.currency`` من لوحة التحكم، ولا يسمح لعطل قراءة
    الإعدادات أن يكسر أي مسار تشغيلي.
    """
    try:
        return system_config()["currency"]
    except Exception:  # noqa: BLE001 — قراءة العملة لا يجب أن تكسر أي عملية
        return _DEFAULTS["billing.currency"]


def format_money(amount: Any, currency: str | None = None) -> str:
    """Format a number with the system currency symbol (unified display)."""
    if amount in (None, ""):
        return "—"
    try:
        n = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    cur = (currency or system_config()["currency"]).upper()
    sym = CURRENCY_SYMBOLS.get(cur, cur)
    s = f"{n:,.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return f"{s} {sym}"


def to_local(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a UTC datetime / ISO string to the configured local time."""
    if not value:
        return "—"
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip().replace("Z", "").replace("T", " ")
        s = s.split(".")[0][:19]
        for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, f)
                break
            except ValueError:
                continue
    if dt is None:
        return str(value)
    try:
        return (dt + timedelta(hours=system_config()["tz_offset"])).strftime(fmt)
    except Exception:  # noqa: BLE001
        return str(value)


def to_local_date(value: Any) -> str:
    return to_local(value, fmt="%Y-%m-%d")


def _ar_days(n: int) -> str:
    """Arabic-correct day count: 1→يوم، 2→يومان، 3-10→أيام، 11+→يوم."""
    if n == 1:
        return "يوم"
    if n == 2:
        return "يومان"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يوم"


def _ar_hours(n: int) -> str:
    """Arabic-correct hour count: 1→ساعة، 2→ساعتان، 3-10→ساعات، 11+→ساعة."""
    if n == 1:
        return "ساعة"
    if n == 2:
        return "ساعتان"
    if 3 <= n <= 10:
        return f"{n} ساعات"
    return f"{n} ساعة"


def _ar_minutes(n: int) -> str:
    """Arabic-correct minute count: 1→دقيقة، 2→دقيقتان، 3-10→دقائق، 11+→دقيقة."""
    if n == 1:
        return "دقيقة"
    if n == 2:
        return "دقيقتان"
    if 3 <= n <= 10:
        return f"{n} دقائق"
    return f"{n} دقيقة"


def format_duration_days(minutes: Any) -> str:
    """Humanize a raw MINUTE count to a friendly Arabic days string.

    Durations across the panel are stored in MINUTES, but operators think
    in DAYS — so 5400 دقيقة becomes «3 أيام و18 ساعة» instead of a wall of
    minutes. Rules:
      • whole days        → «X يوم/أيام» (e.g. 1440 → «يوم», 4320 → «3 أيام»)
      • days + hours      → «X أيام وY ساعة» (e.g. 5400 → «3 أيام و18 ساعة»)
      • < 1 day           → hours (e.g. 90 → «ساعة ونصف»? no — «ساعة»/«ساعات»)
      • < 1 hour          → minutes
      • 0 / invalid / None → «—»
    Used by the `dur_days` Jinja filter (registered in app/__init__.py).
    """
    try:
        m = int(float(minutes or 0))
    except (TypeError, ValueError):
        return "—"
    if m <= 0:
        return "—"
    days = m // 1440
    hours = (m % 1440) // 60
    mins = m % 60
    if days:
        if hours:
            return f"{_ar_days(days)} و{_ar_hours(hours)}"
        return _ar_days(days)
    if hours:
        if mins:
            return f"{_ar_hours(hours)} و{_ar_minutes(mins)}"
        return _ar_hours(hours)
    return _ar_minutes(mins)
