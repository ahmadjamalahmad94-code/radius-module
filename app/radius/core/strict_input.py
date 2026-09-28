"""Strict input parsing shared by the NAS / sessions endpoints (web + API).

Why this exists (stress campaign A08, 2026-09-28):
  * ``bool("false") is True`` — the NAS API enabled a router when the app sent
    ``"enabled": "false"`` / ``"0"``.
  * ``int(True) == 1`` and ``int(1812.9) == 1812`` were silently accepted, and
    a value above 2**63 overflowed SQLite → raw HTML 500.
  * Session timestamps came out half with a trailing ``Z`` and half without,
    so the app parsed some UTC values as local time.

Every helper raises ``RadiusValidationError`` with an Arabic message; the API
maps it to 422 and the web routes flash it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .errors import RadiusValidationError

_TRUE = {"1", "true", "yes", "on", "y", "t", "نعم", "مفعل", "مفعّل"}
_FALSE = {"0", "false", "no", "off", "n", "f", "", "لا", "معطل", "معطّل"}


def parse_strict_bool(value: Any, *, label: str) -> bool:
    """Real booleans, 0/1 and the usual true/false spellings. Anything else
    (``"maybe"``, ``2``, lists…) is rejected instead of being truthy."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون true أو false.")
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
    raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون true أو false.")


def parse_ranged_int(value: Any, *, label: str, minimum: int, maximum: int,
                     default: int | None = None) -> int:
    """An integer inside ``[minimum, maximum]``.

    Accepts ints and digit strings; rejects booleans, fractions (``1812.9``),
    non-numeric text and out-of-range values. ``None``/``""`` → ``default``
    when one is given, otherwise a validation error."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise RadiusValidationError(f"قيمة «{label}» مطلوبة.")
    if isinstance(value, bool):
        raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
    if isinstance(value, int):
        out = value
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")) or not value.is_integer():
            raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
        out = int(value)
    elif isinstance(value, str):
        s = value.strip()
        body = s[1:] if s[:1] in "+-" else s
        if not body.isdigit() or not body.isascii() or len(body) > 18:
            raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
        out = int(s)
    else:
        raise RadiusValidationError(f"قيمة «{label}» يجب أن تكون رقمًا صحيحًا.")
    if out < minimum or out > maximum:
        raise RadiusValidationError(
            f"قيمة «{label}» يجب أن تكون بين {minimum} و{maximum}.")
    return out


def iso_utc_z(value: Any) -> str | None:
    """One timestamp format for API output: ISO-8601 UTC with a trailing ``Z``.

    Accepts naive datetimes (the codebase stores naive UTC), aware datetimes
    (converted to UTC), and the text forms found in ``radacct`` —
    FreeRADIUS «YYYY-MM-DD HH:MM:SS», ISO with/without ``Z`` or an offset.
    Unparseable text is returned unchanged; empty → ``None``."""
    if value is None or value == "":
        return None
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        cand = s.replace(" ", "T", 1)
        if cand.endswith(("Z", "z")):
            cand = cand[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(cand)
        except ValueError:
            return value
    else:
        return str(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    spec = "microseconds" if dt.microsecond else "seconds"
    return dt.isoformat(timespec=spec) + "Z"


__all__ = ["parse_strict_bool", "parse_ranged_int", "iso_utc_z"]
