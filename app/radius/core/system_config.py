"""Single source of truth for system-wide display config: currency, timezone,
system name, country, logo. Read from tenant_settings (editable from the
control panel). Exposed to all templates as `cfg`, plus the `money` and
`dt_local` Jinja filters so currency and time are unified everywhere.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from flask import g

try:  # zoneinfo ships with Python 3.9+; the IANA database itself comes from
    # the `tzdata` package on platforms (Windows) that lack a system one.
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover — defensive; stdlib import should succeed
    ZoneInfo = None  # type: ignore[assignment]

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
    # Primary timezone setting: an IANA zone name (DST-safe via zoneinfo). The
    # owner is Levantine (UTC+3); Asia/Damascus is the default — see FLAG in the
    # PR notes; both Asia/Damascus and Asia/Amman are permanent UTC+3 today.
    "billing.timezone": "Asia/Damascus",
    # Legacy fixed hour offset — kept as a fallback for environments without the
    # IANA database, and for any zone not in the picker. The IANA name wins.
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
        "tz_name": _get("billing.timezone") or _DEFAULTS["billing.timezone"],
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


def _resolve_tzinfo(tz_name: str, tz_offset_hours: float) -> tzinfo:
    """Build a tzinfo for the configured panel timezone.

    Prefer the DST-safe IANA zone (``billing.timezone`` via ``zoneinfo``); fall
    back to a fixed hour offset (legacy ``billing.timezone_offset``) when the
    name is empty, unknown, or the IANA database is unavailable. ``"UTC"`` maps
    to ``timezone.utc`` directly.
    """
    name = (tz_name or "").strip()
    if name.upper() == "UTC":
        return timezone.utc
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — unknown/missing zone → offset fallback
            pass
    return timezone(timedelta(hours=tz_offset_hours))


def _tz_settings(tenant_id: int | None = None) -> tuple[str, float]:
    """Return ``(iana_name, offset_hours)`` for a tenant.

    ``tenant_id is None`` reads the current request tenant via ``g`` (used by
    the Jinja filters during a request); an explicit id is used by background
    workers / the schedule evaluator which run without a request context.
    """
    if tenant_id is None:
        name = _get("billing.timezone")
        raw = _get("billing.timezone_offset") or "3"
    else:
        try:
            from ..db.repos import tenants_repo
            name = str(tenants_repo.get_setting(
                tenant_id, "billing.timezone",
                _DEFAULTS["billing.timezone"]) or "").strip()
            raw = str(tenants_repo.get_setting(
                tenant_id, "billing.timezone_offset", "3") or "3").strip()
        except Exception:  # noqa: BLE001 — settings read must never break logic
            name, raw = _DEFAULTS["billing.timezone"], "3"
    try:
        off = float(raw or 3)
    except (TypeError, ValueError):
        off = 3.0
    return (name or _DEFAULTS["billing.timezone"]), off


def tenant_tzinfo(tenant_id: int | None = None) -> tzinfo:
    """tzinfo for the configured panel timezone (DST-safe IANA, offset fallback)."""
    name, off = _tz_settings(tenant_id)
    return _resolve_tzinfo(name, off)


def _coerce_dt(value: Any) -> datetime | None:
    """Parse a datetime or ISO/space string into a ``datetime`` (or None)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip().replace("Z", "").replace("T", " ")
        s = s.split(".")[0][:19]
        for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, f)
            except ValueError:
                continue
    return None


def to_local(value: Any, fmt: str = "%Y-%m-%d %H:%M",
             tenant_id: int | None = None) -> str:
    """Convert a UTC datetime / ISO string to the configured local time.

    Stored timestamps are naive UTC, so a naive value is treated as UTC and
    converted once (never double-applied). An already-aware datetime is honored
    as-is via ``astimezone``. DST-safe when an IANA zone is configured.
    """
    if not value:
        return "—"
    dt = _coerce_dt(value)
    if dt is None:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(tenant_tzinfo(tenant_id)).strftime(fmt)
    except Exception:  # noqa: BLE001
        return str(value)


def to_local_date(value: Any) -> str:
    return to_local(value, fmt="%Y-%m-%d")


def local_now(tenant_id: int | None = None) -> datetime:
    """Current wall-clock time in the configured panel timezone (aware)."""
    return datetime.now(timezone.utc).astimezone(tenant_tzinfo(tenant_id))


def local_today(tenant_id: int | None = None) -> date:
    """Today's date **as the owner sees it** — not UTC's today.

    🔴 كلّ «اليوم» في التقارير كان ``datetime.utcnow().date()``. وغزّة +3، فمنتصف
       ليل UTC هو الثالثة فجرًا محلّيًّا: تُحسب مبيعات ما بين منتصف الليل والثالثة
       على **يوم الأمس**، فيشكو المالك أنّ «عدّ اليوم يبدأ الساعة ٢ فجرًا».
    """
    return local_now(tenant_id).date()


def local_period_utc_range(period: str, value: str,
                           tenant_id: int | None = None) -> tuple[str, str]:
    """حدود فترةٍ **محلّيّة** معبَّرًا عنها بطوابع UTC — للمقارنة في SQL.

    الطوابع تُخزَّن UTC ساذجةً (``YYYY-MM-DD HH:MM:SS``)، والمشغّل يفكّر بيومه
    المحلّيّ. فمقارنة ``substr(ts,1,10) = '2026-08-07'`` تخلط الاثنين وتُزيح
    النافذة بمقدار الإزاحة الزمنيّة.

    ``period`` ∈ {daily, weekly, monthly, yearly}. ``value`` هو التاريخ المحلّيّ
    (‏``YYYY-MM-DD`` أو ``YYYY-MM`` أو ``YYYY``؛ وللأسبوع تاريخُ بدايته).

    يُعيد ``(start_utc, end_utc)`` نصفَ مفتوحٍ ‎[start, end)‎ — تُستعمل هكذا:
    ``WHERE col >= ? AND col < ?`` بدل ``substr(...) = ?``.
    """
    tz = tenant_tzinfo(tenant_id)
    v = (value or "").strip()
    try:
        if period == "yearly":
            start_l = datetime(int(v[:4]), 1, 1, tzinfo=tz)
            end_l = datetime(int(v[:4]) + 1, 1, 1, tzinfo=tz)
        elif period == "monthly":
            y, m = int(v[:4]), int(v[5:7])
            start_l = datetime(y, m, 1, tzinfo=tz)
            end_l = (datetime(y + 1, 1, 1, tzinfo=tz) if m == 12
                     else datetime(y, m + 1, 1, tzinfo=tz))
        else:  # daily / weekly — كلاهما يبدأ من يومٍ ويمتدّ 1 أو 7 أيّام
            d = date.fromisoformat(v[:10])
            start_l = datetime(d.year, d.month, d.day, tzinfo=tz)
            end_l = start_l + timedelta(days=7 if period == "weekly" else 1)
    except (TypeError, ValueError):
        # قيمةٌ غير صالحة ⇒ يوم اليوم المحلّيّ، فلا يُعيد الاستعلام الكونَ كلّه
        d = local_today(tenant_id)
        start_l = datetime(d.year, d.month, d.day, tzinfo=tz)
        end_l = start_l + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (start_l.astimezone(timezone.utc).strftime(fmt),
            end_l.astimezone(timezone.utc).strftime(fmt))


def local_hhmm(tenant_id: int | None = None, when: Any = None) -> str:
    """``HH:MM`` in the configured panel timezone.

    ``when`` is a UTC instant (naive treated as UTC, or aware); ``None`` → now.
    Used by bandwidth-schedule evaluation so a window like "00:00–06:00" means
    the owner's LOCAL midnight, not UTC midnight.
    """
    if when is None:
        dt: datetime | None = datetime.now(timezone.utc)
    else:
        dt = when if isinstance(when, datetime) else _coerce_dt(when)
        if dt is None:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tenant_tzinfo(tenant_id)).strftime("%H:%M")


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
