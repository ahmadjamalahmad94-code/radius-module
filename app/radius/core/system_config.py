"""Single source of truth for system-wide display config: currency, timezone,
system name, country, logo. Read from tenant_settings (editable from the
control panel). Exposed to all templates as `cfg`, plus the `money` and
`dt_local` Jinja filters so currency and time are unified everywhere.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import functools
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from flask import g

try:  # zoneinfo ships with Python 3.9+; the IANA database itself comes from
    # the `tzdata` package on platforms (Windows) that lack a system one.
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover — defensive; stdlib import should succeed
    ZoneInfo = None  # type: ignore[assignment]

CURRENCY_SYMBOLS = {
    "JOD": N_("د.أ"), "ILS": "₪", "USD": "$", "IQD": N_("د.ع"),
    "SAR": N_("ر.س"), "EGP": N_("ج.م"), "AED": N_("د.إ"), "EUR": "€", "TRY": "₺",
}
CURRENCY_NAMES = {
    "JOD": N_("دينار أردني"), "ILS": N_("شيكل"), "USD": N_("دولار"), "IQD": N_("دينار عراقي"),
    "SAR": N_("ريال سعودي"), "EGP": N_("جنيه مصري"), "AED": N_("درهم"), "EUR": N_("يورو"), "TRY": N_("ليرة"),
}

_DEFAULTS = {
    "billing.currency": "ILS",
    # Primary timezone setting: an IANA zone name (DST-safe via zoneinfo).
    # قرار المالك 2026-09-29: فلسطين — Asia/Gaza (والخليل Asia/Hebron بنفس
    # القواعد): ‎+2 شتاءً و‎+3 صيفًا. لا إزاحة ثابتة أبدًا؛ zoneinfo يحسب الصيفيّ.
    "billing.timezone": "Asia/Gaza",
    # Legacy fixed hour offset — kept as a fallback for environments without the
    # IANA database, and for any zone not in the picker. The IANA name wins.
    "billing.timezone_offset": "3",
    # قرار المالك 2026-09-29: مشتركٌ يُنشأ **بلا** تاريخ انتهاء (نموذج الويب
    # بتاريخ فارغ، ‎POST /accounts بلا مفتاح expire_at، التطبيق، الاستيراد)
    # يولد «منتهيًا» (expire_at = لحظة الإنشاء) — لا حسابٌ دائم بالسهو.
    # «unlimited» = بلا انتهاء (NULL) — لخادم HobeHub المجّانيّ فقط.
    # الاختيار الصريح يغلب دائمًا: «بدون انتهاء» / ‎expire_at: null ⇒ NULL،
    # وتاريخٌ صريح ⇒ هو.
    "subscribers.create_without_expiry": "expired",
    "system.name": "HobeRadius",
    "radius.default_country": "",
    "branding.logo_url": "",
    "branding.primary_color": "#2BAACC",
}


# قائمة العملات في صفحة الإعدادات — الشيكل أولًا (الافتراضيّ).
CURRENCY_CHOICES = [
    ("ILS", N_("شيكل ₪")), ("USD", N_("دولار أمريكي $")), ("JOD", N_("دينار أردني")),
    ("EGP", N_("جنيه مصري")), ("IQD", N_("دينار عراقي")), ("SAR", N_("ريال سعودي")),
    ("AED", N_("درهم إماراتي")), ("EUR", N_("يورو €")), ("TRY", N_("ليرة تركية")),
]

# قائمة المناطق الزمنية (IANA) — فلسطين أولًا. لا نكتب إزاحةً ثابتة في التسمية
# لمنطقةٍ لها توقيتٌ صيفيّ: الإزاحة الحاليّة تُعرض حيّةً بجانب المعاينة.
PANEL_TIMEZONES = [
    ("Asia/Gaza", N_("غزة (فلسطين)")),
    ("Asia/Hebron", N_("الخليل (فلسطين)")),
    ("Asia/Amman", N_("عمّان (الأردن)")),
    ("Asia/Damascus", N_("دمشق (سوريا)")),
    ("Asia/Beirut", N_("بيروت (لبنان)")),
    ("Africa/Cairo", N_("القاهرة (مصر)")),
    ("Asia/Baghdad", N_("بغداد (العراق)")),
    ("Asia/Riyadh", N_("الرياض (السعودية)")),
    ("Asia/Dubai", N_("دبي (الإمارات)")),
    ("Asia/Tehran", N_("طهران (إيران)")),
    ("Europe/Istanbul", N_("إسطنبول (تركيا)")),
    ("UTC", N_("التوقيت العالمي UTC")),
]
PANEL_TIMEZONE_LABELS = dict(PANEL_TIMEZONES)

# «المشترك الجديد بلا تاريخ انتهاء» — القيم المسموحة لـ
# ``subscribers.create_without_expiry`` (الأولى = الافتراضيّ).
CREATE_WITHOUT_EXPIRY_CHOICES = [
    ("expired", N_("منتهٍ فورًا")),
    ("unlimited", N_("بلا انتهاء")),
]
CREATE_WITHOUT_EXPIRY_LABEL = N_("المشترك الجديد بلا تاريخ انتهاء: منتهٍ فورًا / بلا انتهاء")


def create_without_expiry_mode(tenant_id: int | None = None) -> str:
    """``expired`` (الافتراضيّ) أو ``unlimited`` — ما يعنيه إنشاء مشتركٍ لم
    يُعطَ تاريخ انتهاء. قيمةٌ تالفة ⇒ الافتراضيّ الآمن (منتهٍ)."""
    key = "subscribers.create_without_expiry"
    default = _DEFAULTS[key]
    try:
        from ..db.repos import tenants_repo
        tid = int(tenant_id) if tenant_id is not None else _tid()
        val = str(tenants_repo.get_setting(tid, key, default) or "").strip().lower()
    except Exception:  # noqa: BLE001 — settings read must never break a create
        val = default
    return val if val in dict(CREATE_WITHOUT_EXPIRY_CHOICES) else default


def default_new_subscriber_expiry(tenant_id: int | None = None,
                                  now: datetime | None = None) -> datetime | None:
    """نهاية مشتركٍ جديد **لم يُعطَ** تاريخًا (ولم يُطلب «بدون انتهاء» صراحةً):
    لحظة الإنشاء (UTC ساكن) — فيولد منتهيًا — أو ``None`` حين يضبط الخادم
    ``unlimited``. مصدرٌ واحد للإنشاء الحقيقيّ: الويب والـAPI/التطبيق وإنشاء
    المدير/«مستخدمو البطاقات». **لا** يسري على استيراد مايكروتيك ولا معالج
    الترحيل — ينسخان حسابًا قائمًا فيحفظان مصدره (بلا انتهاء = بلا انتهاء)."""
    if create_without_expiry_mode(tenant_id) == "unlimited":
        return None
    return (now or datetime.utcnow()).replace(microsecond=0)


def is_valid_timezone(name: str) -> bool:
    """اسم IANA تعرفه zoneinfo (أو UTC)."""
    name = (name or "").strip()
    if not name:
        return False
    if name.upper() == "UTC":
        return True
    if ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001
        return False


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
    currency = (_get("billing.currency") or "ILS").upper()
    try:
        # الإزاحة الحاليّة للمنطقة (صيفيّ/شتويّ) — لا الاحتياط الثابت.
        tz_offset = effective_timezone()["utc_offset_minutes"] / 60.0
    except Exception:  # noqa: BLE001
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


def effective_timezone(tenant_id: int | None = None,
                       at: datetime | None = None) -> dict[str, Any]:
    """المنطقة الزمنية **الفعليّة** للوحة + إزاحتها الآن عن UTC بالدقائق.

    ``timezone``: اسم IANA صالح دائمًا — المضبوط إن عرفته zoneinfo، وإلّا
    (اسم تالف/قاعدة غائبة) المكافئ الثابت للإزاحة الاحتياطيّة ``Etc/GMT-3``
    (إشارة IANA معكوسة) أو ``UTC``. ``utc_offset_minutes`` تتغيّر مع الصيفيّ
    (غزة: 120 شتاءً، 180 صيفًا). ``at`` لحظة UTC (افتراضًا الآن)."""
    name, off = _tz_settings(tenant_id)
    tz = _resolve_tzinfo(name, off)
    when = at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone(tz)
    delta = local.utcoffset() or timedelta(0)
    minutes = int(delta.total_seconds() // 60)
    if is_valid_timezone(name):
        eff = "UTC" if name.strip().upper() == "UTC" else name.strip()
    elif minutes % 60 == 0 and minutes:
        eff = f"Etc/GMT{-minutes // 60:+d}"
    else:
        eff = "UTC"
    return {
        "timezone": eff,
        "timezone_label": PANEL_TIMEZONE_LABELS.get(eff, eff),
        "utc_offset_minutes": minutes,
        "local_time": local.strftime("%Y-%m-%d %H:%M"),
    }


def effective_system_settings() -> dict[str, Any]:
    """The EFFECTIVE system money/time settings for API clients (the app).

    ``billing.currency`` unset or blank → the default (ILS), exactly what
    ``default_currency()`` records on every new row — so the app never shows
    one currency while the server writes another (stress 2026-09-28: the
    settings API said JOD while every payment was recorded in ILS)."""
    cfg = system_config()
    tz = effective_timezone()
    return {
        "currency": cfg["currency"],
        "currency_symbol": cfg["currency_symbol"],
        "currency_name": cfg["currency_name"],
        # المنطقة الفعليّة (اسم IANA) — التطبيق يعرض/يختار الأوقات بها.
        "timezone": tz["timezone"],
        "timezone_label": tz["timezone_label"],
        # الإزاحة **الحاليّة** بالدقائق (غزة 120 شتاءً / 180 صيفًا).
        "utc_offset_minutes": tz["utc_offset_minutes"],
        "local_time": tz["local_time"],
        # حقلان قديمان للتوافق: الاسم كما هو، والإزاحة الحاليّة بالساعات
        # (لم تعد إزاحة الاحتياط الثابتة — كانت +3 حتى في شتاء غزة).
        "tz_name": tz["timezone"],
        "tz_offset": tz["utc_offset_minutes"] / 60.0,
        # «expired» | «unlimited» — ما يعنيه إنشاء مشتركٍ بلا expire_at.
        "create_without_expiry": create_without_expiry_mode(),
        # F03 N7: القاعدة الواحدة لتحويل وقتٍ محلّيّ مكتوب إلى UTC (الويب يطبّقها
        # في from_local؛ التطبيق يطبّقها بجدول التحوّلات أدناه — لا بإزاحة الآن).
        "local_time_rule": dict(LOCAL_TIME_RULE),
        "tz_transitions": tz_transitions(),
        # «الحدود» — سقوف العمليّة الواحدة لهذا الخادم (core.limits): التطبيق
        # يتحقّق بالأرقام نفسها التي يفرضها الخادم.
        "limits": _limits_snapshot(),
    }


def _limits_snapshot() -> dict[str, Any]:
    try:
        from .limits import snapshot
        return snapshot()
    except Exception:  # noqa: BLE001
        return {}


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


def format_money_multi(by_currency: Any, key: str = "total",
                       fallback: Any = None) -> str:
    """مبلغٌ لكلّ عملة بدل رقمٍ واحد مخلوط: «5,683.89 ₪ · 426.31 USD».

    لا سعر صرف في النظام، فجمعُ ILS+USD+EUR في رقمٍ واحد بعلامة ₪ كذبٌ.
    ``by_currency`` قائمة ``[{"currency": "ILS", key: x}, …]`` (شكل الـAPI) أو
    قاموس ``{"ILS": x}``. عملة النظام بالرمز، والبقيّة برمز ISO. كلّ جزء
    معزول باتّجاه (LRI…PDI) كي لا يتبعثر الترتيب داخل نصّ عربيّ. قائمة فارغة
    → ``fallback`` بعملة النظام."""
    if isinstance(by_currency, dict):
        entries = [{"currency": c, key: v} for c, v in by_currency.items()]
    else:
        entries = [e for e in (by_currency or []) if isinstance(e, dict)]
    try:
        system_cur = system_config()["currency"].upper()
    except Exception:  # noqa: BLE001 — عرضٌ فقط
        system_cur = _DEFAULTS["billing.currency"]
    parts: list[str] = []
    for e in entries:
        try:
            amount = float(e.get(key) or 0)
        except (TypeError, ValueError):
            continue
        cur = str(e.get("currency") or system_cur).upper()
        if len(entries) > 1 and abs(amount) < 0.005:
            continue
        if cur == system_cur:
            text = format_money(amount, cur)
        else:
            text = f"{amount:,.2f} {cur}"
        parts.append("\u2066" + text + "\u2069")
    if not parts:
        return format_money(fallback if fallback is not None else 0)
    return " · ".join(parts)


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


# ── تحويل الوقت المحلّيّ ⇒ UTC: قاعدةٌ واحدة للويب والـAPI والتطبيق (F03 N7) ──
# ليلة انتهاء التوقيت الصيفيّ (غزة 2026-10-24: 02:00+03 ⇒ 01:00+02) تتكرّر
# الساعة 01:00–01:59؛ الويب كان يضع «01:30» عند 22:30Z والتطبيق عند 23:30Z
# (ساعةٌ فرق). القاعدة: **الظهور الأوّل** (fold=0 — إزاحة ما قبل التحوّل:
# 01:30 ⇒ 22:30Z). وليلة بدء الصيفيّ (ساعةٌ لا وجود لها) تُقرأ بإزاحة ما قبل
# التحوّل فتقع بعد القفزة (00:30 غير الموجودة ⇒ 01:30 الصيفيّة). التطبيق يطبّق
# القاعدة بجدول ``tz_transitions`` (لحظات التحوّل وإزاحتا قبل/بعد).
LOCAL_TIME_RULE = {
    "ambiguous": "earlier",
    "nonexistent": "shift_forward",
    "description_ar": (N_("وقتٌ محلّيّ يتكرّر (ليلة انتهاء التوقيت الصيفيّ) يُحتسب بظهوره "
                       "الأوّل — بإزاحة ما قبل التحوّل؛ ووقتٌ غير موجود (ليلة بدء "
                       "الصيفيّ) يُقرأ بإزاحة ما قبل التحوّل فيقع بعد القفزة.")),
}


def tz_transitions(tenant_id: int | None = None, *, at: datetime | None = None,
                   months_back: int = 12, months_ahead: int = 24) -> list[dict]:
    """تحوّلات إزاحة منطقة اللوحة حول ``at`` (افتراضًا الآن): ``[{"at": UTC ISO Z،
    "offset_before_minutes"، "offset_after_minutes"}]``. محصّن: [] عند الخطأ."""
    try:
        name, off = _tz_settings(tenant_id)
        when = (at or datetime.now(timezone.utc))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return [dict(t) for t in _tz_transitions_cached(
            name, float(off), when.year, when.month, int(months_back), int(months_ahead))]
    except Exception:  # noqa: BLE001
        return []


@functools.lru_cache(maxsize=32)
def _tz_transitions_cached(name: str, off: float, year: int, month: int,
                           months_back: int, months_ahead: int) -> tuple:
    tz = _resolve_tzinfo(name, off)
    start_idx = year * 12 + (month - 1) - months_back
    end_idx = year * 12 + (month - 1) + months_ahead
    t = datetime(start_idx // 12, start_idx % 12 + 1, 1, tzinfo=timezone.utc)
    end = datetime(end_idx // 12, end_idx % 12 + 1, 1, tzinfo=timezone.utc)
    step = timedelta(hours=6)

    def _off(x: datetime) -> int:
        return int((x.astimezone(tz).utcoffset() or timedelta(0)).total_seconds() // 60)

    out = []
    prev = _off(t)
    while t < end:
        nxt = t + step
        cur = _off(nxt)
        if cur != prev:
            lo, hi = t, nxt                      # offset(lo) == prev, offset(hi) == cur
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                mid = mid.replace(second=0, microsecond=0)
                if mid <= lo:
                    mid = lo + timedelta(minutes=1)
                if _off(mid) == prev:
                    lo = mid
                else:
                    hi = mid
            out.append({"at": hi.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "offset_before_minutes": prev, "offset_after_minutes": cur})
            prev = cur
        t = nxt
    return tuple(out)


def from_local(value: Any, tenant_id: int | None = None,
               default_time: str = "") -> datetime | None:
    """Inverse of :func:`to_local` — a local wall-clock string -> naive UTC.

    The operator picks «2026-09-01 18:00» meaning six in the evening **where
    they are**. Stored expiries are naive UTC, so a picked value must be
    stamped with the panel timezone and converted exactly once. Storing it
    verbatim — what the day/month/year picker used to do — makes a +3 panel's
    «23:59» land at 02:59 the *next* day: the subscription outlives the day
    its owner chose, by the whole timezone offset.

    Accepts ``YYYY-MM-DD[ T]HH:MM[:SS]`` and a bare ``YYYY-MM-DD``; a bare
    date takes ``default_time`` (``"HH:MM"`` / ``"HH:MM:SS"``) when supplied.
    Returns ``None`` for an empty or unparsable value — callers decide what a
    missing date means (blank never silently becomes «now»).
    """
    s = str(value or "").strip().replace("T", " ")
    if not s:
        return None
    if default_time and len(s) == 10:
        s = f"{s} {default_time}"
    dt = _coerce_dt(s)
    if dt is None:
        return None
    if dt.tzinfo is not None:  # already anchored -> just normalise to UTC
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        # fold=0 صراحةً: الظهور الأوّل للساعة المكرّرة (LOCAL_TIME_RULE).
        return dt.replace(tzinfo=tenant_tzinfo(tenant_id), fold=0).astimezone(
            timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — a broken zone must not lose the date
        return dt


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
        return N_("يوم")
    if n == 2:
        return N_("يومان")
    if 3 <= n <= 10:
        return _tr('%(n)s أيام', n=n)
    return _tr('%(n)s يوم', n=n)


def _ar_hours(n: int) -> str:
    """Arabic-correct hour count: 1→ساعة، 2→ساعتان، 3-10→ساعات، 11+→ساعة."""
    if n == 1:
        return N_("ساعة")
    if n == 2:
        return N_("ساعتان")
    if 3 <= n <= 10:
        return _tr('%(n)s ساعات', n=n)
    return _tr('%(n)s ساعة', n=n)


def _ar_minutes(n: int) -> str:
    """Arabic-correct minute count: 1→دقيقة، 2→دقيقتان، 3-10→دقائق، 11+→دقيقة."""
    if n == 1:
        return N_("دقيقة")
    if n == 2:
        return N_("دقيقتان")
    if 3 <= n <= 10:
        return _tr('%(n)s دقائق', n=n)
    return _tr('%(n)s دقيقة', n=n)


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
            return _tr('%(v)s و%(v2)s', v=_ar_days(days), v2=_ar_hours(hours))
        return _ar_days(days)
    if hours:
        if mins:
            return _tr('%(v)s و%(v2)s', v=_ar_hours(hours), v2=_ar_minutes(mins))
        return _ar_hours(hours)
    return _ar_minutes(mins)
