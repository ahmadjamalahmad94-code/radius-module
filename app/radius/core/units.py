"""Unit conversion helpers for the form-side `unit_input_picker`.

We store all magnitudes in a canonical base unit (kbps for speed, MB for
quota, minutes for time, KB for size). The picker UI lets the operator
type a number and pick a display unit; the macro+JS converts to base
before submitting. These helpers exist so:

  1. Server-side renders (e.g. profile pages) can format base values
     for human display.
  2. The unit list / ratios stay in one place (mirrored by JS in the
     macro — keep both sides in lock-step).

See SERVICES_COOKBOOK §18.
"""
from __future__ import annotations

from typing import Optional

# (code, label, ratio-to-base). Order matters: smallest base first so that
# `best_unit()` can walk from biggest to smallest and pick the largest
# clean division.
SPEED_UNITS: list[tuple[str, str, int]] = [
    ("kbps", "Kbps", 1),
    ("Mbps", "Mbps", 1024),
    ("Gbps", "Gbps", 1024 * 1024),
]
QUOTA_UNITS: list[tuple[str, str, int]] = [
    ("MB", "MB", 1),
    ("GB", "GB", 1024),
    ("TB", "TB", 1024 * 1024),
]
TIME_UNITS: list[tuple[str, str, int]] = [
    ("min",   "دقائق",  1),
    ("hr",    "ساعات",  60),
    ("day",   "أيام",   60 * 24),
    ("month", "شهور",   60 * 24 * 30),
]
SIZE_UNITS: list[tuple[str, str, int]] = [
    ("KB", "KB", 1),
    ("MB", "MB", 1024),
    ("GB", "GB", 1024 * 1024),
]

KINDS: dict[str, list[tuple[str, str, int]]] = {
    "speed": SPEED_UNITS,
    "quota": QUOTA_UNITS,
    "time":  TIME_UNITS,
    "size":  SIZE_UNITS,
}


class UnitError(ValueError):
    pass


def _ratios(kind: str) -> dict[str, int]:
    if kind not in KINDS:
        raise UnitError(f"unknown kind {kind!r}")
    return {code: ratio for code, _label, ratio in KINDS[kind]}


def to_base(value: float, unit: str, kind: str) -> int:
    """Convert a display value+unit to the canonical base unit (int)."""
    r = _ratios(kind)
    if unit not in r:
        raise UnitError(f"unit {unit!r} is not valid for kind {kind!r}")
    return int(round(float(value) * r[unit]))


def from_base(base_value: int, unit: str, kind: str) -> float:
    """Convert a base value back to a given display unit."""
    r = _ratios(kind)
    if unit not in r:
        raise UnitError(f"unit {unit!r} is not valid for kind {kind!r}")
    return float(base_value) / r[unit]


def best_unit(base_value: int, kind: str) -> str:
    """Largest unit that divides ``base_value`` cleanly.

    For 0 → returns the smallest unit (the canonical base). This is the
    "polite" unit a human would type by hand:

      best_unit(5120, "speed")  → "Mbps"
      best_unit(5121, "speed")  → "kbps"   (5121 isn't a clean Mbps)
      best_unit(1024, "quota")  → "GB"
      best_unit(0,    "time")   → "min"
    """
    units = KINDS.get(kind)
    if not units:
        raise UnitError(f"unknown kind {kind!r}")
    if base_value == 0:
        return units[0][0]
    # Walk biggest → smallest, return the first that divides cleanly.
    for code, _label, ratio in sorted(units, key=lambda x: -x[2]):
        if base_value >= ratio and base_value % ratio == 0:
            return code
    return units[0][0]


def format_pair(base_value: int, kind: str) -> tuple[float, str]:
    """Return (display_value, unit_code) chosen via best_unit()."""
    unit = best_unit(base_value, kind)
    return from_base(base_value, unit, kind), unit


def format_human(base_value: int, kind: str) -> str:
    """Human-readable rendering, e.g. ``"5 Mbps"`` / ``"2 ساعات"``."""
    value, unit = format_pair(base_value, kind)
    # Drop trailing .0 for whole numbers.
    if value == int(value):
        value = int(value)
    # Find label for unit
    for code, label, _ratio in KINDS[kind]:
        if code == unit:
            return f"{value} {label}"
    return f"{value} {unit}"


__all__ = [
    "UnitError", "KINDS",
    "SPEED_UNITS", "QUOTA_UNITS", "TIME_UNITS", "SIZE_UNITS",
    "to_base", "from_base", "best_unit",
    "format_pair", "format_human",
]
