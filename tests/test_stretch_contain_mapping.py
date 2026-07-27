"""contain داخل الخانة الممدودة يُحسب عبر الكانفس (client1، 2026-07-27).

الحالة: تصميم مرفوع بملاءمة «إظهار كاملة» + تصدير «تمدد يملأ الخانة».
كانت الصورة تُحتوى داخل الخانة الممدودة بنسبة الصورة الأصلية — فتظهر
أشرطة فارغة يمين/يسار («ما في تمدد أفقي») وتنزاح رسومها عن حقول
اليوزر/الباس التي تتمدد من الكانفس («منزاحات»). الصحيح: صندوق contain
يُحسب داخل الكانفس (نفس المعاينة حرفيًا) ثم يُحوَّل بمقياس الخانة
المستقل لكل محور — فيتشوه مع البطاقة كلها ويبقى مطابقًا للمعاينة.
"""
from __future__ import annotations

import pytest

# 1×1 شفاف PNG (مربع 1:1).
DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
)


class _PdfSpy:
    def __init__(self):
        self.draws = []

    def saveState(self):  # noqa: N802 — reportlab API
        pass

    def restoreState(self):  # noqa: N802
        pass

    def drawImage(self, image, x, y, width=None, height=None, **kw):  # noqa: N802
        self.draws.append((x, y, width, height))

    def setFillColor(self, *a, **k):  # noqa: N802
        pass

    def rect(self, *a, **k):
        pass


def _model(fit: str):
    return {
        "canvas": {"width": 600, "height": 1000},
        "background": {
            "source": "image",
            "image_data_url": DATA_URL,
            "image_fit": fit,
            "image_opacity": 1.0,
        },
    }


def test_stretch_contain_maps_through_canvas():
    from app.radius.services.card_renderer import draw_uploaded_background_uniform

    pdf = _PdfSpy()
    ok = draw_uploaded_background_uniform(
        pdf, _model("contain"), slot_x=10, slot_y=20,
        slot_width=100, slot_height=60, stretch=True)
    assert ok and pdf.draws
    x, y, w, h = pdf.draws[0]
    # صورة مربعة داخل كانفس 600×1000: صندوق contain = (0, 200, 600, 600)
    # ثم يتحول بمقياس الخانة sx=100/600 وsy=60/1000 مع قلب محور y:
    assert x == pytest.approx(10 + 0)
    assert y == pytest.approx(20 + (1000 - 200 - 600) * (60 / 1000))
    assert w == pytest.approx(600 * (100 / 600))
    assert h == pytest.approx(600 * (60 / 1000))


def test_stretch_stretchfit_fills_slot():
    from app.radius.services.card_renderer import draw_uploaded_background_uniform

    pdf = _PdfSpy()
    ok = draw_uploaded_background_uniform(
        pdf, _model("stretch"), slot_x=5, slot_y=6,
        slot_width=90, slot_height=70, stretch=True)
    assert ok and pdf.draws
    assert pdf.draws[0] == (5, 6, 90, 70)


def test_uniform_contain_unchanged():
    from app.radius.services.card_renderer import draw_uploaded_background_uniform

    pdf = _PdfSpy()
    ok = draw_uploaded_background_uniform(
        pdf, _model("contain"), slot_x=0, slot_y=0,
        slot_width=60, slot_height=100, stretch=False)
    assert ok and pdf.draws
    x, y, w, h = pdf.draws[0]
    # خانة موحّدة بنسبة الكانفس (0.6): البطاقة 60×100، الصورة المربعة
    # تُحتوى: 60×60 ممركزة عموديًا.
    assert (w, h) == (pytest.approx(60), pytest.approx(60))
    assert y == pytest.approx(20)
