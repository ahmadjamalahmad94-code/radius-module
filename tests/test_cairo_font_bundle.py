# -*- coding: utf-8 -*-
"""يونيو 2026 — Cairo (Google Fonts، SIL OFL) هو الـbundled الأساسي
للنَصّ العَربي على الكَروت + صَفحات الـhotspot. تَنقيح المالك: «جَرّب
Cairo بَدل Almarai المَفقود». الـTTFs مَوجودة في app/static/fonts،
الـwoff2 في app/static/hotspot/fonts. هذه الاختبارات تَضمن:

  • الملفات مَوجودة فعليًّا (لا regression بحَذف فاتنة عند إعادة
    تَنظيم الـrepo).
  • تَفتح كـTTFs صَحيحة (fonttools).
  • ReportLab يُسَجّل الخَطّ تَحت اسم PDF_FONT_ARABIC (Cairo يَنطبق
    بدلاً من Almarai في الـregistration).
  • الـHotspot CSS @font-face يَحقن Cairo + Almarai.
"""
from __future__ import annotations

import os

import pytest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATIC_FONTS = os.path.join(_PROJECT_ROOT, "app", "static", "fonts")
_HOTSPOT_FONTS = os.path.join(_PROJECT_ROOT, "app", "static", "hotspot",
                                "fonts")


# ════════════════════════════════════════════════════════════════════════
# (1) Cairo TTFs لـPDF/SVG rendering
# ════════════════════════════════════════════════════════════════════════
class TestCairoTTFBundle:

    def test_cairo_regular_exists(self):
        p = os.path.join(_STATIC_FONTS, "Cairo-Regular.ttf")
        assert os.path.isfile(p), f"Cairo-Regular.ttf مَفقود: {p}"
        assert os.path.getsize(p) > 10_000, "ملف صَغير شُكّ في صَلاحيته"

    def test_cairo_bold_exists(self):
        p = os.path.join(_STATIC_FONTS, "Cairo-Bold.ttf")
        assert os.path.isfile(p)
        assert os.path.getsize(p) > 10_000

    def test_cairo_black_exists(self):
        """Cairo-Black يُلَبّي font-weight 900/950 للعَناوين."""
        p = os.path.join(_STATIC_FONTS, "Cairo-Black.ttf")
        assert os.path.isfile(p)
        assert os.path.getsize(p) > 10_000

    def test_cairo_files_are_valid_ttf(self):
        """يَفتح بنجاح عبر fontTools؟ التَحقّق من سَلامة الـTTF."""
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            pytest.skip("fontTools not available")
        for name in ("Cairo-Regular.ttf", "Cairo-Bold.ttf",
                      "Cairo-Black.ttf"):
            path = os.path.join(_STATIC_FONTS, name)
            f = TTFont(path)
            # كل خَطّ يَجب أن يَحوي جداول cmap وglyf أساسيّة
            assert "cmap" in f
            assert "name" in f
            family = f["name"].getDebugName(1)
            assert family and "Cairo" in family, \
                f"{name}: family name غير Cairo ({family!r})"

    def test_ofl_license_present(self):
        """رُخصة SIL OFL مَوجودة (شَرط استعمال Cairo + Almarai)."""
        p = os.path.join(_STATIC_FONTS, "OFL.txt")
        assert os.path.isfile(p), "OFL.txt مَفقود — رُخصة الخَطّ مَطلوبة"


# ════════════════════════════════════════════════════════════════════════
# (2) ReportLab registration يُفَضّل Cairo
# ════════════════════════════════════════════════════════════════════════
class TestRendererPrefersCairo:

    def test_ensure_arabic_fonts_registers_cairo(self):
        """يُسَجّل الـTTF Cairo تَحت اسم PDF_FONT_ARABIC."""
        from app.radius.services import card_renderer
        # نُعيد ضَبط الـcache كي يُعاد التَسجيل (إنّه global module-level)
        card_renderer._arabic_fonts_ready = None
        card_renderer._arabic_extrabold_ready = False
        assert card_renderer._ensure_arabic_fonts() is True
        # الـTTFont المُسجَّل في ReportLab يَجب أن يَعرف الاسم
        from reportlab.pdfbase import pdfmetrics
        # Almarai هو الـPDF_FONT_ARABIC name (للتَوافق)؛ الـtrue file
        # المُرتبط Cairo. نَتحقّق أنّ الاسم مُسَجَّل (لا KeyError).
        try:
            face = pdfmetrics.getFont(card_renderer.PDF_FONT_ARABIC)
            assert face is not None
        except KeyError as e:
            pytest.fail(f"PDF_FONT_ARABIC غير مُسَجَّل: {e}")

    def test_cairo_paths_constants_exist(self):
        from app.radius.services import card_renderer
        assert os.path.isfile(card_renderer._CAIRO_REGULAR_PATH)
        assert os.path.isfile(card_renderer._CAIRO_BOLD_PATH)
        assert os.path.isfile(card_renderer._CAIRO_BLACK_PATH)

    def test_svg_font_family_lists_cairo_first(self):
        """قائمة font-family في SVG يَجب أن تَبدأ بـCairo."""
        from app.radius.services.operations import _template_layout
        from app.radius.services.card_renderer import (
            build_card_render_model, render_card_svg)
        layout = _template_layout({"design_preset": "clinic_trust",
                                     "render_engine": "ar_horizontal"})
        template = {"id": 1, "name": "t", "orientation": "landscape",
                     "layout_json": layout}
        model = build_card_render_model(template,
            {"id": "915", "username": "card-915", "password": "Pw_9152"})
        svg = render_card_svg(model)
        # Cairo قَبل Almarai في الـstack
        cairo_pos = svg.find("'Cairo'")
        almarai_pos = svg.find("'Almarai'")
        assert cairo_pos > 0, "Cairo غَير مَذكور في font-family"
        if almarai_pos > 0:
            assert cairo_pos < almarai_pos, \
                "Cairo يَجب أن يَسبق Almarai في الـstack"


# ════════════════════════════════════════════════════════════════════════
# (3) Hotspot woff2 + injection
# ════════════════════════════════════════════════════════════════════════
class TestHotspotCairoWOFF2:

    def test_cairo_woff2_files_exist(self):
        for name in ("Cairo-Regular.woff2", "Cairo-Bold.woff2"):
            p = os.path.join(_HOTSPOT_FONTS, name)
            assert os.path.isfile(p), f"{p} مَفقود (مَطلوب للهوت سبوت)"
            assert os.path.getsize(p) > 5_000

    def test_almarai_woff2_still_present(self):
        """fallback يَبقى — حَذف Almarai كان سَيَكسر القَوالب القَديمة."""
        for name in ("Almarai-Regular.woff2", "Almarai-Bold.woff2"):
            p = os.path.join(_HOTSPOT_FONTS, name)
            assert os.path.isfile(p)

    def test_font_face_css_contains_cairo(self):
        from app.radius.services.hotspot_templates import FONT_FACE_CSS
        assert "Cairo" in FONT_FACE_CSS
        assert "Cairo-Regular.woff2" in FONT_FACE_CSS
        assert "Cairo-Bold.woff2" in FONT_FACE_CSS
        # Almarai يَبقى fallback
        assert "Almarai" in FONT_FACE_CSS

    def test_cairo_files_in_almarai_files_const_or_separate(self):
        """ALMARAI_FONT_FILES يَبقى كَما هو (التَوافق)، الـCairo files
        في CAIRO_FONT_FILES."""
        from app.radius.services.hotspot_templates import (
            ALMARAI_FONT_FILES, CAIRO_FONT_FILES)
        assert "fonts/Cairo-Regular.woff2" in CAIRO_FONT_FILES
        assert "fonts/Cairo-Bold.woff2" in CAIRO_FONT_FILES
        assert "fonts/Almarai-Regular.woff2" in ALMARAI_FONT_FILES

    def test_inject_fontface_adds_cairo_block(self):
        """render() يَحقن الـ@font-face Cairo + Almarai في صَفحة تَستعمل
        font-family Cairo."""
        from app.radius.services.hotspot_templates import (
            inject_almarai_fontface)
        html = (
            "<html><head><style>"
            "body{font-family:'Cairo',Tahoma,sans-serif}"
            "</style></head><body></body></html>"
        )
        out = inject_almarai_fontface(html)
        assert "@font-face{font-family:'Cairo'" in out
        # Almarai أيضًا مَحقون (fallback)
        assert "@font-face{font-family:'Almarai'" in out
