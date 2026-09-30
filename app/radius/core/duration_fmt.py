"""Abbreviated duration formatting with LATIN unit letters (RTL/bidi-safe).

Arabic single-letter unit abbreviations («س» hour, «د» minute, «ي» day,
«ث» second, «ش» month) get bidi-scrambled when placed next to Latin digits
inside an RTL page: bidi rule W2 turns the digit that follows an Arabic
letter into an Arabic-context number, so «1h 38m / 4h» rendered with Arabic
letters flips into unreadable «س4 / د38 س1».

Latin unit letters (h / m / d / s / mo) are strong-LTR characters — exactly
like the digits — so a token like ``1h 38m`` stays one left-to-right run and
never reverses, in any direction context. Callers should still wrap the token
in an LTR isolate (``<bdi dir="ltr">`` / ``dir="ltr"``) as a belt-and-suspenders
measure; these helpers return the plain Latin text so they are safe in text,
attribute, JSON and KPI-value contexts alike.

This is the single source of truth for abbreviated durations — every route,
service and template that shows a compact session time / uptime should format
through here so the unit letters stay consistent.
"""
from __future__ import annotations

# Canonical Latin unit letters. Keep unambiguous: month is "mo" (not "m",
# which is minute); second is "s"; day is "d"; hour is "h"; minute is "m".
UNIT_DAY = "d"
UNIT_HOUR = "h"
UNIT_MINUTE = "m"
UNIT_SECOND = "s"
UNIT_MONTH = "mo"


def fmt_hm_short(seconds) -> str:
    """Hours/minutes only, e.g. ``"1h 5m"`` / ``"2h"`` / ``"30m"``.

    Used by the «وقت اليوم» (daily-usage) column. Mirrors the historic
    ``_fmt_hm`` behaviour exactly — only the unit letters changed to Latin.
    """
    sec = max(0, int(seconds or 0))
    h, m = sec // 3600, (sec % 3600) // 60
    if h and m:
        return f"{h}{UNIT_HOUR} {m}{UNIT_MINUTE}"
    return f"{h}{UNIT_HOUR}" if h else f"{m}{UNIT_MINUTE}"


def fmt_uptime_short(seconds) -> str:
    """Days/hours/minutes, e.g. ``"2d 3h 5m"`` / ``"3h 5m"`` / ``"5m"``.

    Used by system/router uptime displays. Mirrors the historic
    ``_format_uptime`` / ``format_duration`` behaviour — Latin letters only.
    """
    total = max(0, int(seconds or 0))
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}{UNIT_DAY} {h}{UNIT_HOUR} {m}{UNIT_MINUTE}"
    if h:
        return f"{h}{UNIT_HOUR} {m}{UNIT_MINUTE}"
    return f"{m}{UNIT_MINUTE}"


def fmt_compact(seconds) -> str:
    """Days/hours/minutes with ZERO components dropped — the tightest
    bidi-safe token for a used/total pair: ``432000 -> "5d"``,
    ``5400 -> "1h 30m"``, ``10800 -> "3h"``, ``90 -> "1m"``, ``0 -> "0m"``.

    Used by the «وقت اليوم» used/total badge where ``fmt_uptime_short``'s
    padded form ("5d 0h 0m") is too noisy and ``fmt_hm_short`` cannot show a
    whole-day card budget as days ("120h").
    """
    total = max(0, int(seconds or 0))
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _sec = divmod(rem, 60)
    parts = [f"{n}{u}" for n, u in
             ((d, UNIT_DAY), (h, UNIT_HOUR), (m, UNIT_MINUTE)) if n]
    return " ".join(parts) if parts else f"0{UNIT_MINUTE}"


def _ar_plural(n: int, one: str, two: str, few: str, many: str) -> str:
    """Arabic count word for ``n`` (1 → one, 2 → dual, 3–10 → few, 11+ → many)."""
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return few
    return many


def fmt_remaining_ar(seconds) -> str:
    """الوقت المتبقّي بكلماتٍ عربيّة لأكبر وحدتين غير صفريّتين.

    ``90000 -> "1 يوم و1 ساعة"``، ``5400 -> "1 ساعة و30 دقيقة"``،
    ``7200 -> "2 ساعتان"``… أقلّ من دقيقة ⇒ «أقل من دقيقة». لا «0 يوم» أبدًا
    (f05-L5: بطاقةٌ بقيت لها ساعات كانت تُخبَر «(0 يوم)»). رقمٌ لاتينيّ بجوار
    كلمةٍ عربيّة لا ينقلب بالاتّجاه، فلا حاجة لعزل."""
    s = max(0, int(seconds or 0))
    if s < 60:
        return "أقل من دقيقة"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    units = [(d, ('يوم', 'يومان', 'أيام', 'يومًا')),
             (h, ('ساعة', 'ساعتان', 'ساعات', 'ساعة')),
             (m, ('دقيقة', 'دقيقتان', 'دقائق', 'دقيقة'))]
    # «2 ساعتان» ركيك: المثنّى يُقرأ وحده («ساعتان»، «يومان»، «دقيقتان»).
    parts = [(_ar_plural(n, *w) if n == 2 else f"{n} {_ar_plural(n, *w)}")
             for n, w in units if n]
    return " و".join(parts[:2])


def fmt_compact_ar(seconds) -> str:
    """Arabic-word twin of :func:`fmt_compact` for the web panel (f06-L3: the
    /online columns read «5m» and «8m / ∞» — English units). Two largest
    non-zero units in words; ``0`` → «0 دقيقة». A Latin digit next to an
    Arabic WORD never bidi-flips, so no LTR isolate is needed."""
    s = max(0, int(seconds or 0))
    if s < 60:
        return "0 دقيقة"
    return fmt_remaining_ar(s)


def fmt_duration_ar(seconds) -> str:
    """Session/usage duration in Arabic words for reports and profiles (fix3
    integration: rep_sessions / users_profile / portal_subscriber now match
    /online). Same words as :func:`fmt_compact_ar` from one minute up; a short
    session keeps its seconds («45 ثانية») instead of «0 دقيقة»; 0 → «0 دقيقة»."""
    s = max(0, int(seconds or 0))
    if s <= 0:
        return "0 دقيقة"
    if s < 60:
        w = _ar_plural(s, "ثانية", "ثانيتان", "ثوانٍ", "ثانية")
        return w if s == 2 else f"{s} {w}"
    return fmt_compact_ar(s)


def fmt_base_time_ar(seconds) -> tuple[str, bool]:
    """Human-friendly Arabic label for a card's BASE (total) time budget.

    This is the ORIGINAL time allotment («وقت البطاقة») — the from-first-connect
    budget resolved from the card's batch, NOT the remaining countdown. It reads
    most naturally as full Arabic words for a whole single unit, and falls back
    to the shared bidi-safe Latin abbreviation for a mixed / sub-unit budget.

    Returns a ``(text, is_latin)`` tuple:

      * ``("", False)`` when there is no time budget (``seconds`` <= 0) — e.g. a
        calendar-validity card. The caller shows «حسب الصلاحية» / «—» instead of 0.
      * A whole single unit → Arabic words, ``is_latin=False``:
        ``10800 -> ("3 ساعات", False)``, ``1800 -> ("30 دقيقة", False)``,
        ``432000 -> ("5 أيام", False)``.
      * A mixed / seconds-level budget → the shared Latin abbreviation
        (:func:`fmt_uptime_short`), ``is_latin=True``: ``5880 -> ("1h 38m", True)``.
        The caller wraps a Latin result in ``<bdi dir="ltr">`` so the digits and
        unit letters never bidi-flip inside an RTL page. A lone digit followed by
        an Arabic word does not flip, so whole-unit words need no wrapper.
    """
    s = max(0, int(seconds or 0))
    if s <= 0:
        return "", False
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d and not (h or m or sec):
        return f"{d} {_ar_plural(d, 'يوم', 'يومان', 'أيام', 'يومًا')}", False
    if h and not (d or m or sec):
        return f"{h} {_ar_plural(h, 'ساعة', 'ساعتان', 'ساعات', 'ساعة')}", False
    if m and not (d or h or sec):
        return f"{m} {_ar_plural(m, 'دقيقة', 'دقيقتان', 'دقائق', 'دقيقة')}", False
    # Mixed budget → full Arabic words joined by «و» («1 يوم و3 ساعات و45
    # دقيقة»). The Latin «1d 3h 45m» token reached the card checker as raw
    # English (re-test R13 L4); a digit next to an Arabic WORD never flips.
    parts = []
    if d:
        parts.append(f"{d} {_ar_plural(d, 'يوم', 'يومان', 'أيام', 'يومًا')}")
    if h:
        parts.append(f"{h} {_ar_plural(h, 'ساعة', 'ساعتان', 'ساعات', 'ساعة')}")
    if m:
        parts.append(f"{m} {_ar_plural(m, 'دقيقة', 'دقيقتان', 'دقائق', 'دقيقة')}")
    if sec:
        parts.append(f"{sec} {_ar_plural(sec, 'ثانية', 'ثانيتان', 'ثوانٍ', 'ثانية')}")
    return " و".join(parts), False
