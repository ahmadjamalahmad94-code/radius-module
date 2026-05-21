"""Access schedule — unified day/time windows for subscribers, groups, plans.

A *schedule* is a JSON-serializable dict with a single key ``windows``,
each window having:

  - ``days``: list of day codes (sat,sun,mon,tue,wed,thu,fri). Empty → all days.
  - ``from``: ``HH:MM`` start time (24h). Empty → 00:00.
  - ``to``  : ``HH:MM`` end time (24h). Empty → 24:00.

Semantics:

  - ``windows`` empty / missing → access ALLOWED (no restriction).
  - For each window, "allowed at time T on day D" means
    ``D in days (or days empty)`` AND ``from <= T < to``
    (windows are inclusive of start, exclusive of end).
  - Access overall = ANY window allows it (windows are OR-ed).
  - If ``to <= from`` the window wraps past midnight (e.g. 22:00 → 02:00
    means 22:00–24:00 and 00:00–02:00).

This module owns:

  - ``DAYS``: canonical list of day codes.
  - ``DAY_NAMES_AR``: Arabic labels per code.
  - ``parse(raw)``: normalize a raw value (str/dict/None) into a dict.
  - ``serialize(schedule)``: dict → JSON string for DB storage.
  - ``validate(schedule)``: raises on malformed input.
  - ``is_allowed(schedule, when)``: boolean test for a datetime.
  - ``derive_working_days(schedule)``: CSV cache for the legacy column.

See SERVICES_COOKBOOK §17.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time
from typing import Iterable, Optional

DAYS: tuple[str, ...] = ("sat", "sun", "mon", "tue", "wed", "thu", "fri")
DAY_NAMES_AR: dict[str, str] = {
    "sat": "السبت", "sun": "الأحد",   "mon": "الإثنين", "tue": "الثلاثاء",
    "wed": "الأربعاء", "thu": "الخميس", "fri": "الجمعة",
}
# Python's weekday() returns Mon=0..Sun=6. Map to our codes.
_PY_WEEKDAY_TO_CODE: dict[int, str] = {
    0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun",
}

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class AccessScheduleError(ValueError):
    """Raised when a schedule is malformed and cannot be normalized."""


# ─────────────────────────── parsing ────────────────────────────
def _parse_time(s: str) -> Optional[time]:
    """Parse 'HH:MM' or '' → time object. Empty → None (= no time bound)."""
    s = (s or "").strip()
    if not s:
        return None
    m = _TIME_RE.match(s)
    if not m:
        raise AccessScheduleError(f"وقت غير صالح: {s!r} (المتوقع HH:MM)")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise AccessScheduleError(f"وقت خارج النطاق: {s!r}")
    return time(h, mm)


def _fmt_time(t: Optional[time]) -> str:
    return "" if t is None else f"{t.hour:02d}:{t.minute:02d}"


def _normalize_days(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [d.strip().lower() for d in raw.split(",") if d.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(d).strip().lower() for d in raw if str(d).strip()]
    else:
        raise AccessScheduleError(f"days يجب أن تكون قائمة، وُجد: {type(raw).__name__}")
    seen, out = set(), []
    for d in items:
        if d not in DAYS:
            raise AccessScheduleError(f"يوم غير معروف: {d!r}")
        if d not in seen:
            seen.add(d); out.append(d)
    # Keep canonical order (sat..fri)
    return [d for d in DAYS if d in seen]


def parse(raw) -> dict:
    """Normalize any plausible representation into a clean schedule dict.

    Accepts: None, "", JSON string, dict. Returns ``{"windows": [...]}``
    with each window having ``days``/``from``/``to`` as strings.
    """
    if raw is None or raw == "":
        return {"windows": []}
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AccessScheduleError(f"JSON غير صالح: {e}") from e
    elif isinstance(raw, dict):
        data = raw
    else:
        raise AccessScheduleError(f"النوع غير مدعوم: {type(raw).__name__}")

    wins_raw = data.get("windows") if isinstance(data, dict) else None
    if wins_raw is None:
        return {"windows": []}
    if not isinstance(wins_raw, list):
        raise AccessScheduleError("windows يجب أن تكون قائمة")

    out_windows: list[dict] = []
    for i, w in enumerate(wins_raw):
        if not isinstance(w, dict):
            raise AccessScheduleError(f"window #{i} ليس dict")
        days = _normalize_days(w.get("days"))
        t_from = _parse_time(w.get("from", ""))
        t_to   = _parse_time(w.get("to",   ""))
        out_windows.append({
            "days": days,
            "from": _fmt_time(t_from),
            "to":   _fmt_time(t_to),
        })
    return {"windows": out_windows}


def serialize(schedule: dict) -> str:
    """Validated dict → JSON string for DB storage. Empty schedule → ''."""
    norm = parse(schedule)  # idempotent normalization
    if not norm["windows"]:
        return ""
    return json.dumps(norm, ensure_ascii=False, separators=(",", ":"))


def validate(schedule) -> dict:
    """Raises AccessScheduleError on malformed input; returns the normalized
    dict on success. Use this on the route boundary."""
    return parse(schedule)


# ─────────────────────────── evaluation ────────────────────────
def _window_allows(window: dict, code: str, t: time) -> bool:
    days = window.get("days") or []
    if days and code not in days:
        return False
    t_from = _parse_time(window.get("from", ""))
    t_to   = _parse_time(window.get("to",   ""))
    # Both empty → window is full-day for these days.
    if t_from is None and t_to is None:
        return True
    f = t_from or time(0, 0)
    e = t_to   or time(23, 59, 59, 999999)
    if e > f:
        return f <= t < e
    # Cross-midnight: e.g. 22:00 → 02:00 means [22:00,24:00) ∪ [00:00,02:00)
    return t >= f or t < e


def is_allowed(schedule, when: Optional[datetime] = None) -> bool:
    """Whether the schedule allows access at ``when`` (default: now).

    Empty schedule = always allowed.
    """
    sched = parse(schedule)
    windows = sched.get("windows") or []
    if not windows:
        return True
    when = when or datetime.now()
    code = _PY_WEEKDAY_TO_CODE[when.weekday()]
    t = when.time().replace(second=0, microsecond=0)
    return any(_window_allows(w, code, t) for w in windows)


# ─────────────────────────── helpers for callers ────────────────
def derive_working_days(schedule) -> str:
    """Union of all days mentioned in any window → CSV (canonical order).

    Empty schedule or any window with no day restriction → returns "" so the
    legacy ``working_days`` column means "all days" by default (consistent
    with prior behaviour).
    """
    sched = parse(schedule)
    windows = sched.get("windows") or []
    if not windows:
        return ""
    # If any window restricts no days, schedule covers every day.
    if any(not (w.get("days") or []) for w in windows):
        return ""
    seen = set()
    for w in windows:
        for d in (w.get("days") or []):
            seen.add(d)
    return ",".join(d for d in DAYS if d in seen)


def empty_schedule() -> dict:
    """Convenience constructor for a fresh empty schedule."""
    return {"windows": []}


__all__ = [
    "AccessScheduleError",
    "DAYS", "DAY_NAMES_AR",
    "parse", "serialize", "validate",
    "is_allowed", "derive_working_days", "empty_schedule",
]
