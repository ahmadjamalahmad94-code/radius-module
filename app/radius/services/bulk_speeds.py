"""Shared validation for «تعديل سرعات الباقات جماعيًّا» (web /tools/set_speeds
and API /tools/set-speeds).

Stress campaign A08 (2026-09-28): the API accepted ``mult_down: 1e20`` (preview
showed 4e23 kbps, the real run crashed with an HTML 500 on SQLite overflow),
``mult_down: 0`` was silently treated as 1, and ``set_down`` had no ceiling.
"""
from __future__ import annotations

import math
from typing import Any

from ..core.errors import RadiusValidationError

MAX_PLAN_KBPS = 1_000_000      # 1 Gbit — same ceiling as the temp-speed tool
MAX_MULTIPLIER = 100.0


def _multiplier(raw: Any, label: str) -> float:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 1.0
    if isinstance(raw, bool):
        raise RadiusValidationError(f"«{label}» يجب أن يكون رقمًا.")
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        raise RadiusValidationError(f"«{label}» يجب أن يكون رقمًا.") from None
    if not math.isfinite(val) or val <= 0:
        raise RadiusValidationError(f"«{label}» يجب أن يكون رقمًا موجبًا.")
    if val > MAX_MULTIPLIER:
        raise RadiusValidationError(f"«{label}» كبير جدًا (الحد الأقصى {int(MAX_MULTIPLIER)}).")
    return val


def _fixed(raw: Any, label: str) -> int:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 0
    if isinstance(raw, bool):
        raise RadiusValidationError(f"«{label}» يجب أن يكون رقمًا صحيحًا.")
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        raise RadiusValidationError(f"«{label}» يجب أن يكون رقمًا صحيحًا.") from None
    if not math.isfinite(val) or val < 0 or not val.is_integer():
        raise RadiusValidationError(f"«{label}» يجب أن يكون رقمًا صحيحًا موجبًا.")
    if val > MAX_PLAN_KBPS:
        raise RadiusValidationError(
            f"«{label}» أكبر من الحد الأقصى ({MAX_PLAN_KBPS:,} كيلوبت/ث = 1 جيجابت).")
    return int(val)


def parse_bulk_speed_params(mult_down: Any, mult_up: Any,
                            set_down: Any, set_up: Any) -> tuple[float, float, int, int]:
    """→ (mult_down, mult_up, set_down, set_up). Raises RadiusValidationError."""
    return (
        _multiplier(mult_down, "مضاعف التنزيل"),
        _multiplier(mult_up, "مضاعف الرفع"),
        _fixed(set_down, "سرعة التنزيل الثابتة"),
        _fixed(set_up, "سرعة الرفع الثابتة"),
    )


def new_speed(current: Any, mult: float, fixed: int, *, plan_name: str = "",
              direction: str = "") -> int:
    """The new plan speed: the fixed value when given, else current × mult.
    A result above the ceiling is refused (never stored)."""
    if fixed:
        return fixed
    try:
        base = int(current or 0)
    except (TypeError, ValueError):
        base = 0
    out = int(base * mult)
    # 0 kbps means «بلا حدّ» in the plan: a tiny multiplier (0.0001) must not
    # silently turn a limited plan into an unlimited one (re-test R07 N4).
    if base > 0 and out < 1:
        raise RadiusValidationError(
            f"الناتج لسرعة {direction} في الباقة «{plan_name}» أقل من 1 كيلوبت/ث "
            "(الصفر يعني سرعة بلا حدّ) — استخدم مضاعفًا أكبر.")
    if out > MAX_PLAN_KBPS:
        raise RadiusValidationError(
            f"الناتج لسرعة {direction} في الباقة «{plan_name}» ({out:,} كيلوبت/ث) "
            f"يتجاوز الحد الأقصى ({MAX_PLAN_KBPS:,}).")
    return out


__all__ = ["MAX_PLAN_KBPS", "parse_bulk_speed_params", "new_speed"]
