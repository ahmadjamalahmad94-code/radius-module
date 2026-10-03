"""طبقة ترجمةٍ واحدة لرسائل الخدمات — الويب والـAPI يعرضان العربيّة نفسها.

الرسائل تُكتب بالعربيّة **من المصدر** في طبقة الخدمات (users/accounting/…)؛
هذا الجدول شبكة أمانٍ لأيّ رسالة إنجليزيّة قديمة ما زالت تصل من خدمةٍ لم
تُعرَّب بعد (أو من قاعدة بياناتٍ/إضافةٍ خارجيّة). كان الـAPI يملك جدوله
الخاصّ (``_SERVICE_MSG_AR``) بينما يعرض الويب ``e.message`` خامًا — فظهر
«amount must be > 0» و«minutes > 0 required» في نوافذ الويب ووميضه.

الاستعمال: ``flash(error_message_ar(e), "error")`` في الويب، و``fail(...,
error_message_ar(e))`` في الـAPI.
"""
from __future__ import annotations
from app.i18n_text import N_

import re
from typing import Any

SERVICE_MSG_AR: dict[str, str] = {
    "amount must be > 0": N_("المبلغ يجب أن يكون أكبر من صفر."),
    "minutes > 0 required": N_("المدّة يجب أن تكون أكبر من صفر."),
    "expire_at required": N_("تاريخ الانتهاء مطلوب."),
    "unknown extend charge mode": N_("طريقة الإضافة غير معروفة."),
    "unknown quota charge mode": N_("طريقة الإضافة غير معروفة."),
    "unknown reset charge mode": N_("طريقة الاستعادة غير معروفة."),
    "quota_mb must be > 0": N_("حجم الكوتة يجب أن يكون أكبر من صفر."),
    "unknown quota target": N_("نوع الكوتة غير معروف."),
    "plan_id required": N_("اختر العرض الجديد."),
    "unknown plan change policy": N_("طريقة تغيير العرض غير معروفة."),
    "selected plan is not cheaper": N_("العرض المختار ليس أرخص من الحالي."),
    "selected plan is not more expensive": N_("العرض المختار ليس أغلى من الحالي."),
    "plan price and duration are required for this option":
        N_("هذا الخيار يتطلّب سعرًا ومدّة للعرضين."),
    "unsupported message channel": N_("قناة الإرسال غير مدعومة."),
    "message required": N_("نص الرسالة مطلوب."),
    "subscriber mobile is empty": N_("لا يوجد رقم جوال لهذا المشترك."),
    "subscriber id required": N_("المشترك غير صالح."),
    "rounding_mode must be floor, ceil, or nearest":
        N_("طريقة التقريب غير معروفة (floor أو ceil أو nearest)."),
    "unsupported report type": N_("نوع التقرير غير مدعوم."),
    "report snapshot not found": N_("اللقطة غير موجودة."),
    "username required for RADIUS apply": N_("اسم المستخدم مطلوب."),
    "minutes must be > 0 for RADIUS apply": N_("المدّة يجب أن تكون أكبر من صفر."),
    "name is required": N_("الاسم مطلوب."),
    "plan_id is required": N_("اختر العرض."),
    "subscriber_username is required": N_("اسم المشترك مطلوب."),
    "subscriber has no plan_id; set plan_id first": N_("المشترك بلا عرض — حدّد العرض أولًا."),
}

# رسائل ``_to_float``/``_to_int`` القديمة ذات الحقل المتغيّر:
#   «amount must be >= 0.01» / «hours must be an integer» / «custom_price must be a number»
_FIELD_PATTERNS = (
    (re.compile(r"^(\w+) must be >= (-?[\d.]+)$"), N_("قيمة «{f}» يجب ألّا تقلّ عن {n}.")),
    (re.compile(r"^(\w+) must be > (-?[\d.]+)$"), N_("قيمة «{f}» يجب أن تكون أكبر من {n}.")),
    (re.compile(r"^(\w+) must be an integer$"), N_("قيمة «{f}» يجب أن تكون عددًا صحيحًا.")),
    (re.compile(r"^(\w+) must be a number$"), N_("قيمة «{f}» يجب أن تكون رقمًا.")),
    (re.compile(r"^(\w+) (?:is )?required$"), N_("قيمة «{f}» مطلوبة.")),
)


def translate_service_message(message: Any) -> str:
    """رسالة خدمة → العربيّة (الرسالة العربيّة أصلًا تمرّ كما هي)."""
    text = str(message or "").strip()
    if not text:
        return text
    hit = SERVICE_MSG_AR.get(text)
    if hit:
        return hit
    from .numbers import field_label
    for pattern, template in _FIELD_PATTERNS:
        m = pattern.match(text)
        if m:
            groups = m.groups()
            n = groups[1] if len(groups) > 1 else ""
            if n.endswith(".0"):
                n = n[:-2]
            return template.format(f=field_label(groups[0]), n=n)
    return text


def error_message_ar(error: Any) -> str:
    """نصّ الخطأ للمشغّل (RadiusError أو أيّ استثناء) بالعربيّة."""
    return translate_service_message(getattr(error, "message", None) or str(error))


__all__ = ["SERVICE_MSG_AR", "error_message_ar", "translate_service_message"]
