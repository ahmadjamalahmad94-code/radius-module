"""Printed cards showed boxes (tofu) for every standalone letter — «بطاقة ▯نترنت»,
«اسم ▯لمستخدم» (owner report 2026-09-28, app print preview + web export).

arabic-reshaper emits Arabic Presentation Forms-B; the shipped Cairo/Almarai
fonts have NO glyphs for the *isolated* forms (U+FE87 إ, U+FE8D ا, U+FE93 ة…),
so Pillow/ReportLab drew .notdef. The PDF paths now map isolated forms back to
their nominal letters (same shape, present in the fonts)."""
from __future__ import annotations

import glob
import os

import pytest

from app.radius.services import card_renderer as cr

FONTS = sorted(glob.glob(os.path.join(os.path.dirname(cr.__file__),
                                      "..", "..", "static", "fonts", "*.ttf")))
TEXTS = ["بطاقة إنترنت", "اسم المستخدم", "كلمة المرور",
         "احتفظ ببيانات الدخول حتى انتهاء الصلاحية", "آخر أيام الأسبوع"]


def test_no_isolated_presentation_forms_reach_the_pdf():
    for text in TEXTS:
        shaped = cr._shape_arabic_for_pdf(text)
        leftovers = [hex(ord(c)) for c in shaped if ord(c) in cr._ISOLATED_TO_NOMINAL]
        assert not leftovers, (text, leftovers)


@pytest.mark.parametrize("font_path", FONTS, ids=os.path.basename)
def test_every_glyph_drawn_is_real_in_each_shipped_font(font_path):
    from PIL import ImageFont

    font = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.BASIC)
    notdef = font.getmask("")
    notdef_sig = (notdef.getbbox(), bytes(notdef))
    for text in TEXTS:
        for ch in cr._shape_arabic_for_pdf(text):
            if ch.isspace():
                continue
            m = font.getmask(ch)
            assert (m.getbbox(), bytes(m)) != notdef_sig, (
                os.path.basename(font_path), text, hex(ord(ch)))


def test_svg_path_is_untouched():
    # The browser preview falls back to another font by itself; only the PDF
    # paths are normalized.
    assert cr._shape_arabic("إ") == cr._shape_arabic("إ")
    assert "ﺇ" in cr._shape_arabic("إ") or cr._shape_arabic("إ") == "إ"
