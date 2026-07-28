"""MT71 — وحدة مدة الباقة تُشتقّ من الدقائق ولا تتقادم.

منتقي المدة في النموذج يُحوّل «4 ساعات» إلى 240 ويُرسل ``duration_minutes``
وحده، فكان العمودان ``duration_value``/``duration_unit`` يبقيان ``0 Mins``
أو يتقادمان بعد أيّ تعديل ⇒ اللوحة تعرض «240 دقيقة» بدل «4 ساعات».

العقد: الاشتقاق **عرضٌ فقط** — التنفيذ يبقى على ``duration_minutes``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("flask")


def _dto(minutes):
    """يبني الـDTO عبر مسار النموذج الحقيقيّ بأقلّ حقولٍ ممكنة."""
    from app import create_app
    from app.radius.routes import plans as plans_routes

    app = create_app()
    with app.test_request_context(
            "/admin/radius/plans", method="POST",
            data={"name": "ب", "duration_minutes": str(minutes)}):
        return plans_routes._form_to_dto()


@pytest.mark.parametrize("minutes,value,unit", [
    (5, 5, "Mins"),          # أقلّ من ساعة ⇒ دقائق (طلب المالك)
    (59, 59, "Mins"),
    (60, 1, "Hrs"),
    (240, 4, "Hrs"),         # «4 ساعات»
    (900, 15, "Hrs"),        # «15 ساعة»
    (1440, 1, "Days"),       # «24 ساعة» = يوم
    (10080, 7, "Days"),      # أسبوعيّ
    (43200, 30, "Days"),     # شهريّ
    (90, 90, "Mins"),        # ساعة ونصف: لا تُكسَر لكسورٍ مضلّلة
    (0, 0, "Mins"),          # بلا مدة
])
def test_unit_is_derived_from_minutes(minutes, value, unit):
    d = _dto(minutes)
    assert (d.duration_value, d.duration_unit) == (value, unit)


def test_enforcement_field_is_untouched():
    """🔴 الأهمّ: الاشتقاق عرضيّ — الدقائق (مصدر Session-Timeout) كما هي."""
    for m in (5, 240, 10080):
        assert _dto(m).duration_minutes == m
