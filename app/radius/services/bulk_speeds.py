"""Shared validation for «تعديل سرعات الباقات جماعيًّا» (web /tools/set_speeds
and API /tools/set-speeds).

Stress campaign A08 (2026-09-28): the API accepted ``mult_down: 1e20`` (preview
showed 4e23 kbps, the real run crashed with an HTML 500 on SQLite overflow),
``mult_down: 0`` was silently treated as 1, and ``set_down`` had no ceiling.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import math
from typing import Any

from ..core.errors import RadiusValidationError

MAX_PLAN_KBPS = 1_000_000      # 1 Gbit — same ceiling as the temp-speed tool
MAX_MULTIPLIER = 100.0


def _multiplier(raw: Any, label: str) -> float:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 1.0
    if isinstance(raw, bool):
        raise RadiusValidationError(_tr('«%(label)s» يجب أن يكون رقمًا.', label=label))
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        raise RadiusValidationError(_tr('«%(label)s» يجب أن يكون رقمًا.', label=label)) from None
    if not math.isfinite(val) or val <= 0:
        raise RadiusValidationError(_tr('«%(label)s» يجب أن يكون رقمًا موجبًا.', label=label))
    if val > MAX_MULTIPLIER:
        raise RadiusValidationError(_tr('«%(label)s» كبير جدًا (الحد الأقصى %(v)s).', label=label, v=int(MAX_MULTIPLIER)))
    return val


def _fixed(raw: Any, label: str) -> int:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 0
    if isinstance(raw, bool):
        raise RadiusValidationError(_tr('«%(label)s» يجب أن يكون رقمًا صحيحًا.', label=label))
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        raise RadiusValidationError(_tr('«%(label)s» يجب أن يكون رقمًا صحيحًا.', label=label)) from None
    if not math.isfinite(val) or val < 0 or not val.is_integer():
        raise RadiusValidationError(_tr('«%(label)s» يجب أن يكون رقمًا صحيحًا موجبًا.', label=label))
    if val > MAX_PLAN_KBPS:
        raise RadiusValidationError(
            _tr('«%(label)s» أكبر من الحد الأقصى (%(MAX_PLAN_KBPS)s كيلوبت/ث = 1 جيجابت).', label=label, MAX_PLAN_KBPS=format(MAX_PLAN_KBPS, ',')))
    return int(val)


def parse_bulk_speed_params(mult_down: Any, mult_up: Any,
                            set_down: Any, set_up: Any) -> tuple[float, float, int, int]:
    """→ (mult_down, mult_up, set_down, set_up). Raises RadiusValidationError."""
    return (
        _multiplier(mult_down, N_("مضاعف التنزيل")),
        _multiplier(mult_up, N_("مضاعف الرفع")),
        _fixed(set_down, N_("سرعة التنزيل الثابتة")),
        _fixed(set_up, N_("سرعة الرفع الثابتة")),
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
            _tr('الناتج لسرعة %(direction)s في الباقة «%(plan_name)s» أقل من 1 كيلوبت/ث (الصفر يعني سرعة بلا حدّ) — استخدم مضاعفًا أكبر.', direction=direction, plan_name=plan_name))
    if out > MAX_PLAN_KBPS:
        raise RadiusValidationError(
            _tr('الناتج لسرعة %(direction)s في الباقة «%(plan_name)s» (%(out)s كيلوبت/ث) يتجاوز الحد الأقصى (%(MAX_PLAN_KBPS)s).', direction=direction, plan_name=plan_name, out=format(out, ','), MAX_PLAN_KBPS=format(MAX_PLAN_KBPS, ',')))
    return out


__all__ = ["MAX_PLAN_KBPS", "parse_bulk_speed_params", "new_speed"]
