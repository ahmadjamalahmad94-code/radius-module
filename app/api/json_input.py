"""Strict JSON-body helpers shared by API views (stress-fix 2026-09-28).

``request.get_json(silent=True) or {}`` hands a list/scalar body straight to
views that then call ``body.get(...)`` / ``**body`` → an HTML 500. These
helpers turn every malformed shape into a clean Arabic 422 JSON error.

Usage::

    body, err = json_object()
    if err:
        return err
"""
from __future__ import annotations

import math
from typing import Any

from flask import request

from .responses import fail

NOT_OBJECT_MESSAGE = "جسم الطلب يجب أن يكون كائن JSON (مفاتيح وقيم)."


class InputError(ValueError):
    """A single field failed validation; ``message`` is Arabic, user-facing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def json_object():
    """``(dict, None)`` for an object body (missing/empty body → ``{}``),
    ``(None, 422 response)`` for a list / scalar / non-JSON-object body."""
    body = request.get_json(silent=True, force=True)
    if body is None:
        raw = request.get_data(cache=True) or b""
        if raw.strip():
            return None, fail("validation_error", NOT_OBJECT_MESSAGE, status=422)
        return {}, None
    if not isinstance(body, dict):
        return None, fail("validation_error", NOT_OBJECT_MESSAGE, status=422)
    return body, None


def opt_int(value: Any, *, label: str, minimum: int | None = None,
            maximum: int | None = 2**62) -> int | None:
    """``None``/``""`` → ``None``; an integral int/str → int; anything else
    (dict, list, bool, 1.5, "abc", out of range) raises :class:`InputError`."""
    if value in (None, ""):
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        raise InputError(f"قيمة {label} يجب أن تكون رقمًا صحيحًا.")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise InputError(f"قيمة {label} يجب أن تكون رقمًا صحيحًا.")
        value = int(value)
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise InputError(f"قيمة {label} يجب أن تكون رقمًا صحيحًا.")
    if minimum is not None and number < minimum:
        raise InputError(f"قيمة {label} يجب ألا تقل عن {minimum}.")
    if maximum is not None and number > maximum:
        raise InputError(f"قيمة {label} أكبر من المسموح.")
    return number


def opt_text(value: Any, *, label: str, max_len: int | None = None,
             default: str = "") -> str:
    """``None`` → ``default``; str/int/float → stripped text; dict/list/bool
    raise :class:`InputError`. ``max_len`` rejects (never silently truncates)."""
    if value is None:
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        raise InputError(f"قيمة {label} يجب أن تكون نصًا.")
    text = str(value).strip()
    if max_len is not None and len(text) > max_len:
        raise InputError(f"{label} أطول من المسموح ({max_len} حرفًا كحدّ أقصى).")
    return text
