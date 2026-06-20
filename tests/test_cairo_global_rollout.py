# -*- coding: utf-8 -*-
"""يونيو 2026 — تَنقيح المالك «اعتمد الخط ... عممها»: Cairo هو الخَطّ
الرَسمي لكامل اللوحة + كل التَصديرات (PDF/Excel/CSV). هذه الاختبارات
تَتَأكّد أنّ:

  1. cairo_font.css مَوجود + يَحوي @font-face لكل أوزان Cairo + يُشير
     إلى ملفات الـTTF الـbundled (لا URLs خارجيّة).
  2. كل الصَفحات (admin layout + login + portal_* + api/docs + pay_demo)
     تَستورد cairo_font.css بدلاً من Google Fonts CDN.
  3. لا روابط fonts.googleapis في أيّ template.
  4. الـPDF/Excel/CSV exports تَستعمل Cairo:
       - pdf_theme.FONT_AR = HobeCairo (مَرتبط بـCairo TTF)
       - card_renderer PDF_FONT_ARABIC مَرتبط بـCairo TTF (لا Almarai)
       - table_export يَستعمل pdf_theme
  5. كل preset كَرت و67 gallery template هوت سبوت يَنطبق على pattern
     بـ30٪ default.

شغّل وحده."""
from __future__ import annotations

import os
import re

import pytest


_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES = os.path.join(_PROJECT, "app", "templates")
_STATIC_FONTS = os.path.join(_PROJECT, "app", "static", "fonts")
_CSS_DIR = os.path.join(_PROJECT, "app", "static", "css")


# ════════════════════════════════════════════════════════════════════════
# (1) cairo_font.css — الـbase file
# ════════════════════════════════════════════════════════════════════════
class TestCairoFontCSS:

    def test_cairo_font_css_exists(self):
        p = os.path.join(_CSS_DIR, "cairo_font.css")
        assert os.path.isfile(p), f"cairo_font.css مَفقود: {p}"

    def test_cairo_font_css_has_all_weights(self):
        p = os.path.join(_CSS_DIR, "cairo_font.css")
        with open(p, encoding="utf-8") as f:
            css = f.read()
        # كل أوزان Cairo (400/500/600/700/800/900) مَوجودة كَـ@font-face
        for w in (400, 500, 600, 700, 800, 900):
            assert f"font-weight:{w};" in css, f"وَزن {w} مَفقود"

    def test_cairo_font_css_self_hosted_only(self):
        """لا روابط خارجيّة — كل src من /static/fonts/."""
        p = os.path.join(_CSS_DIR, "cairo_font.css")
        with open(p, encoding="utf-8") as f:
            css = f.read()
        # كل src يَجب أن يَكون مَحَلّيًّا
        assert "https://" not in css, "روابط خارجيّة في cairo_font.css"
        assert "/static/fonts/Cairo-Regular.ttf" in css
        assert "/static/fonts/Cairo-Bold.ttf" in css
        assert "/static/fonts/Cairo-Black.ttf" in css

    def test_cairo_font_css_sets_body_font(self):
        """يُعَيِّن Cairo كَالـفont-family الافتراضيّ للـbody."""
        p = os.path.join(_CSS_DIR, "cairo_font.css")
        with open(p, encoding="utf-8") as f:
            css = f.read()
        assert "html, body" in css or "html,body" in css
        # Cairo قَبل أيّ بَديل
        body_block = css.split("html, body")[1] if "html, body" in css \
                      else css.split("html,body")[1]
        assert '"Cairo"' in body_block

    def test_bundled_cairo_ttfs_present(self):
        for name in ("Cairo-Regular.ttf", "Cairo-Bold.ttf",
                      "Cairo-Black.ttf"):
            p = os.path.join(_STATIC_FONTS, name)
            assert os.path.isfile(p), f"{p} مَفقود"


# ════════════════════════════════════════════════════════════════════════
# (2) كل الـtemplates تَستورد cairo_font.css ولا Google Fonts
# ════════════════════════════════════════════════════════════════════════
_TEMPLATES_WITH_FONTS = [
    "admin/_admin_layout.html",
    "radius/login.html",
    "radius/pay_demo.html",
    "radius/portal_card.html",
    "radius/portal_card_login.html",
    "radius/portal_subscriber.html",
    "radius/portal_subscriber_login.html",
    "api/docs.html",
]


class TestTemplatesUseLocalCairo:

    @pytest.mark.parametrize("tpl", _TEMPLATES_WITH_FONTS)
    def test_template_loads_cairo_font_css(self, tpl):
        p = os.path.join(_TEMPLATES, tpl)
        with open(p, encoding="utf-8") as f:
            html = f.read()
        assert "css/cairo_font.css" in html, \
            f"{tpl} لا يَستورد cairo_font.css"

    @pytest.mark.parametrize("tpl", _TEMPLATES_WITH_FONTS)
    def test_template_has_no_google_fonts_link(self, tpl):
        p = os.path.join(_TEMPLATES, tpl)
        with open(p, encoding="utf-8") as f:
            html = f.read()
        assert "fonts.googleapis" not in html, \
            f"{tpl} ما زال يَستعمل Google Fonts CDN"
        assert "fonts.gstatic" not in html

    def test_no_template_in_app_uses_google_fonts(self):
        """مَسح شامل لكل ملف HTML تَحت app/templates/."""
        bad: list[str] = []
        for root, _dirs, files in os.walk(_TEMPLATES):
            for name in files:
                if not name.endswith(".html"):
                    continue
                p = os.path.join(root, name)
                with open(p, encoding="utf-8", errors="ignore") as f:
                    if "fonts.googleapis" in f.read():
                        bad.append(os.path.relpath(p, _PROJECT))
        assert not bad, f"templates ما زالت تَستعمل Google Fonts: {bad}"


# ════════════════════════════════════════════════════════════════════════
# (3) PDF/Excel/CSV exports يَستعملون Cairo
# ════════════════════════════════════════════════════════════════════════
class TestPdfExportsUseCairo:

    def test_pdf_theme_registers_cairo(self):
        from app.radius.services import pdf_theme
        # نُعيد ضَبط الـcache كي يَجري التَسجيل
        pdf_theme._fonts_ready = None
        assert pdf_theme._ensure_fonts() is True
        # FONT_AR هو الاسم في ReportLab
        from reportlab.pdfbase import pdfmetrics
        face = pdfmetrics.getFont(pdf_theme.FONT_AR)
        assert face is not None
        # الـTTF المُرتبط هو Cairo (Cairo-Regular.ttf)
        # نَتَأكّد عبر paths الـconstants
        assert "Cairo-Regular.ttf" in pdf_theme._CAIRO_REGULAR

    def test_card_renderer_prefers_cairo(self):
        from app.radius.services import card_renderer
        card_renderer._arabic_fonts_ready = None
        card_renderer._arabic_extrabold_ready = False
        assert card_renderer._ensure_arabic_fonts() is True
        # الـpaths constants تُشير لـCairo
        assert "Cairo-Regular.ttf" in card_renderer._CAIRO_REGULAR_PATH
        assert os.path.isfile(card_renderer._CAIRO_REGULAR_PATH)

    def test_table_export_uses_pdf_theme(self):
        """_build_pdf يَستورد من pdf_theme."""
        p = os.path.join(_PROJECT, "app", "radius", "routes",
                          "table_export.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        assert "from ..services.pdf_theme import" in src
        assert "build_premium_pdf" in src

    def test_table_export_xlsx_uses_cairo_font_name(self):
        """_build_xlsx يَستعمل Cairo في openpyxl Font."""
        p = os.path.join(_PROJECT, "app", "radius", "routes",
                          "table_export.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        assert 'Font(name="Cairo"' in src

    def test_accounting_uses_pdf_theme(self):
        p = os.path.join(_PROJECT, "app", "radius", "services",
                          "accounting.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        assert "from .pdf_theme import" in src
        assert "build_premium_pdf" in src

    def test_cards_routes_use_pdf_theme(self):
        for p in (
            os.path.join(_PROJECT, "app", "radius", "routes", "cards.py"),
            os.path.join(_PROJECT, "app", "api", "v1", "cards.py"),
        ):
            with open(p, encoding="utf-8") as f:
                src = f.read()
            assert "pdf_theme import build_batches_pdf" in src \
                or "from ..services.pdf_theme import build_batches_pdf" in src \
                or "from ...radius.services.pdf_theme import build_batches_pdf" in src


# ════════════════════════════════════════════════════════════════════════
# (4) Pattern coverage @ 30% across all card presets + hotspot gallery
# ════════════════════════════════════════════════════════════════════════
class TestPatternCoverageGlobal:

    def test_every_card_preset_has_pattern_bg_at_30pct(self):
        from app.radius.services.operations import (
            _PRINT_PRESETS, _template_layout)
        from app.radius.services.card_renderer import (
            build_card_render_model)
        gaps = []
        bad_opacity = []
        for key in _PRINT_PRESETS:
            layout = _template_layout({
                "design_preset": key,
                "render_engine": "ar_horizontal",
            })
            template = {"id": 1, "name": "t", "orientation": "landscape",
                         "layout_json": layout}
            model = build_card_render_model(template,
                {"id": "1", "username": "u", "password": "p"})
            pat = next((e for e in model["elements"]
                        if e.get("id") == "pattern_bg"), None)
            if pat is None:
                gaps.append(key)
            elif abs(pat["opacity"] - 0.30) > 0.001:
                bad_opacity.append((key, pat["opacity"]))
        assert not gaps, f"presets بلا pattern_bg: {gaps}"
        assert not bad_opacity, \
            f"presets بـopacity != 0.30: {bad_opacity[:5]}"

    def test_every_hotspot_gallery_resolves_motif(self):
        from app.radius.services import hotspot_gallery as hg
        gaps = []
        for t in hg.GALLERY:
            _slug, vars_, _addons = hg.resolve(t.key)
            if not vars_.get("MOTIF_ICON"):
                gaps.append(t.key)
        assert not gaps, f"hotspot gallery بلا motif: {gaps}"

    def test_hotspot_default_opacity_is_30pct(self):
        from app.radius.services.hotspot_templates import VARIABLES_BY_SLUG
        assert VARIABLES_BY_SLUG["MOTIF_WATERMARK_OPACITY"].default == "0.30"

    def test_card_default_opacity_is_30pct(self):
        from app.radius.services.operations import _template_layout
        layout = _template_layout({"design_preset": "modern"})
        assert abs(layout["watermark_opacity"] - 0.30) < 0.001
