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
from app.i18n_text import N_, _tr

import math
from typing import Any

from flask import request

from .responses import fail

NOT_OBJECT_MESSAGE = N_("جسم الطلب يجب أن يكون كائن JSON (مفاتيح وقيم).")
TOO_DEEP_MESSAGE = N_("جسم الطلب متداخل أكثر من المسموح.")


class InputError(ValueError):
    """A single field failed validation; ``message`` is Arabic, user-facing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def json_object():
    """``(dict, None)`` for an object body (missing/empty body → ``{}``),
    ``(None, 422 response)`` for a list / scalar / non-JSON-object body."""
    try:
        body = request.get_json(silent=True, force=True)
    except RecursionError:
        return None, fail("validation_error", TOO_DEEP_MESSAGE, status=422)
    if body is None:
        raw = request.get_data(cache=True) or b""
        if raw.strip():
            return None, fail("validation_error", NOT_OBJECT_MESSAGE, status=422)
        return {}, None
    if not isinstance(body, dict):
        return None, fail("validation_error", NOT_OBJECT_MESSAGE, status=422)
    return body, None


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

#: نقاطٌ تقبل مصفوفةَ JSON عُلويّةً قصدًا فلا يسري عليها حارسُ «كائن فقط»
#: (‏/devices/ingest يقبل مصفوفةَ عقودِ DHCP أو كائنًا فيه ``leases``).
_ARRAY_BODY_PATHS = frozenset({"/api/v1/devices/ingest"})


def install_global_json_object_guard(bp) -> None:
    """حارسٌ مركزيّ: جسمُ JSON ليس كائنًا ⇒ 422 عربيّة قبل أيّ معالج.

    ‏``request.get_json(silent=True) or {}`` يحمي من الأشكالِ «الكاذبة» فقط
    (‏``{}`` و``[]`` و``null`` كلُّها falsy فتصير ‎{}‎)، أمّا جسمٌ نصّيٌّ صالحٌ
    مثل ‎``"x"``‎ فهو **صادق** فيبقى ``str``، ثمّ يُنادى ``body.get(...)`` →
    ‏AttributeError → 500 بـHTML. سبرُ الجولةِ الخامسة وجدها على 25 نقطةَ
    كتابةٍ، وفي ‎/api/v1/tenants‎ كانت رسالةُ بايثون الداخليّة تُعرَض للمستخدم.

    مركزيٌّ قصدًا: ‏90 موضعًا يستعمل الصيغةَ الخامّة، وأيُّ نقطةٍ جديدةٍ
    ستُحمى تلقائيًّا بلا تعديلِ توقيعها — نفسُ نهجِ
    ``install_global_api_auth_guard``. النقاطُ التي تتوقّع قائمةً (إن وُجدت)
    تقرأ الجسمَ بنفسها عبر ``get_json`` فلا يمسُّها الحارس إلّا بالرفض، ولذلك
    يقتصر على مسارات /api وطرقِ الكتابة.
    """
    @bp.before_app_request
    def _global_json_object():  # noqa: ANN202
        p = request.path or ""
        if p != "/api" and not p.startswith("/api/"):
            return None
        if request.method not in _BODY_METHODS:
            return None
        if p.rstrip("/") in _ARRAY_BODY_PATHS:
            return None
        if not (request.mimetype or "").endswith("json"):
            return None  # نماذج/ملفّات — ليست حِمْلَ JSON
        raw = request.get_data(cache=True) or b""
        if not raw.strip():
            return None  # جسمٌ فارغ: المعالجاتُ تعتبره {}
        try:
            body = request.get_json(silent=True)
        except RecursionError:
            # a 2,000-level nesting overflowed json's C decoder → it was an HTML 500
            return fail("validation_error", TOO_DEEP_MESSAGE, status=422)
        if isinstance(body, dict):
            return None
        if body is None and raw.strip() not in (b"null",):
            return None  # JSON معطوب — يتركُه المعالجُ لقاعدتِه الخاصّة
        return fail("validation_error", NOT_OBJECT_MESSAGE, status=422)


def opt_int(value: Any, *, label: str, minimum: int | None = None,
            maximum: int | None = 2**62) -> int | None:
    """``None``/``""`` → ``None``; an integral int/str → int; anything else
    (dict, list, bool, 1.5, "abc", out of range) raises :class:`InputError`."""
    if value in (None, ""):
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        raise InputError(_tr('قيمة %(label)s يجب أن تكون رقمًا صحيحًا.', label=label))
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise InputError(_tr('قيمة %(label)s يجب أن تكون رقمًا صحيحًا.', label=label))
        value = int(value)
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise InputError(_tr('قيمة %(label)s يجب أن تكون رقمًا صحيحًا.', label=label))
    if minimum is not None and number < minimum:
        raise InputError(_tr('قيمة %(label)s يجب ألا تقل عن %(minimum)s.', label=label, minimum=minimum))
    if maximum is not None and number > maximum:
        raise InputError(_tr('قيمة %(label)s أكبر من المسموح.', label=label))
    return number


def opt_text(value: Any, *, label: str, max_len: int | None = None,
             default: str = "") -> str:
    """``None`` → ``default``; str/int/float → stripped text; dict/list/bool
    raise :class:`InputError`. ``max_len`` rejects (never silently truncates)."""
    if value is None:
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        raise InputError(_tr('قيمة %(label)s يجب أن تكون نصًا.', label=label))
    text = str(value).strip()
    if max_len is not None and len(text) > max_len:
        raise InputError(_tr('%(label)s أطول من المسموح (%(max_len)s حرفًا كحدّ أقصى).', label=label, max_len=max_len))
    return text
