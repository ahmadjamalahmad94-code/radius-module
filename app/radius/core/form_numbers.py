"""أرقام نماذج الويب بمحارف عربيّة — تطبيعٌ خادميّ لا يعتمد على JavaScript.

🔴 R12 N1: لوحة الجوّال العربيّة تكتب «٣٥٫٥» (الفاصلة العشريّة U+066B). كان
``latin_digits.js`` يحذف «٫» فتصير الدفعة 355 (+152 يومًا). أُصلح الـ JS، لكن
الخادم يجب أن يُخرج النتيجة الصحيحة حتى لو لم يعمل (نسخة مخزّنة قديمة، صفحة
بلا السكربت، إرسال بلا حدث input). مسارات الويب تمرّر نصّ الحقل كما هو إلى
الخدمات (``float(amount)``) — فنطبّع مرّةً واحدة عند الباب:

  • كل حقلٍ في نموذج POST قيمته **رقمٌ صِرف** فيه محرفٌ عربيّ/فارسيّ
    (٠-٩ · ۰-۹ · «٫» · «٬» · «−») ⇒ صيغته اللاتينيّة: «٣٥٫٥» ⇒ «35.5»،
    «١٬٠٠٠» ⇒ «1000».
  • أيّ نصٍّ آخر لا يُلمس حرفيًّا (اسم، ملاحظة، «1,5» الملتبسة…).
  • حقول الأسرار (كلمة مرور/سرّ/توكن) لا تُلمس أبدًا — قيمتها تُطابَق حرفيًّا.
  • ``/api/`` خارج النطاق (JSON؛ محلّلاته تمرّ بـ ``core.numbers``).
"""
from __future__ import annotations

import re

from .numbers import normalize_numeric_text

_SECRET_FIELD = re.compile(r"pass|secret|pwd|token|otp", re.IGNORECASE)
_FORM_MIMETYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def normalize_form_numbers(form):
    """نسخةٌ من ``form`` (MultiDict) بقيَمٍ رقميّة مطبَّعة، أو ``None`` إن لم
    يتغيّر شيء (فلا نستبدل كائن الطلب بلا داعٍ)."""
    changed = False
    items = []
    for key, value in form.items(multi=True):
        if isinstance(value, str) and not _SECRET_FIELD.search(key or ""):
            new = normalize_numeric_text(value)
            if new != value:
                changed = True
                value = new
        items.append((key, value))
    if not changed:
        return None
    return type(form)(items)


def install_form_number_normalizer(app) -> None:
    from flask import request

    @app.before_request
    def _normalize_arabic_form_numbers():
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        if request.path.startswith("/api/"):
            return None
        if request.mimetype not in _FORM_MIMETYPES:
            return None
        try:
            fresh = normalize_form_numbers(request.form)
        except Exception:  # noqa: BLE001 — التطبيع تحسينٌ؛ لا يُسقط طلبًا
            return None
        if fresh is not None:
            # ``form``/``values`` خصائص مخزَّنة في Werkzeug: نستبدل المخزَّن.
            request.__dict__["form"] = fresh
            request.__dict__.pop("values", None)
        return None


__all__ = ["install_form_number_normalizer", "normalize_form_numbers"]
