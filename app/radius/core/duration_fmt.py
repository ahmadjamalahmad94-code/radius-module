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
