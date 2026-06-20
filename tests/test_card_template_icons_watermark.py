# -*- coding: utf-8 -*-
"""feat/card-template-icons — اختبارات «خَلفيّة نَمطيّة قِطاعيّة» للكَروت
+ حارس عَدم تَداخل العنوان والـQR.

السياق (يونيو 2026، تَنقيح المالك المُتعَدّد):
  • الإصدار الأوّل: brand_icon بارز + watermark — رَفضه («دفش ومبالغ»).
  • الإصدار الثاني: watermark single-shape هَامِسة — قَريب لكن ما زال
    «شَكلًا واحدًا كَبيرًا».
  • الإصدار الحاليّ (هذا الفِرع): **خَلفيّة نَمطيّة قابلة للتَكرار** من
    6 motifs خَطّيّة دَقيقة لكلّ قطاع، بشَفافيّة ~6% — مَثل النَمط
    المَوصوف في الـreferences (تَدفّق فناجين/حُبوب/مَلاعق على بَطاقة كافيه).

الإصلاح الأصلي «دخول الإنترنت» يَحدث الـQR ما زال مَحمولًا (heading_width
يَضِيق عند ظُهور الـQR).

شغّل وحده (عزل الاختبارات لكل ملف)."""
from __future__ import annotations

import re

import pytest

from app.radius.services import operations as ops
from app.radius.services.card_renderer import build_card_render_model, render_card_svg
from app.radius.services import card_motif_patterns as cmp
from app.radius.services.card_motifs import (
    resolve_motif, VERTICAL_TO_MOTIF,
)
from app.radius.services.card_template_gallery import GALLERY_META, GALLERY_PRESETS


def _build_layout(key: str, *, engine: str = "ar_horizontal", **over) -> dict:
    return ops._template_layout({
        "design_preset": key, "render_engine": engine, **over,
    })


def _build_model(key: str, *, engine: str = "ar_horizontal", **over):
    layout = _build_layout(key, engine=engine, **over)
    template = {
        "id": 1, "name": "t", "orientation": "portrait",
        "cards_per_row": 2, "cards_per_column": 5, "page_size": "A4",
        "font_size": 12, "color": "#111", "show_qr": True,
        "username_x": 0, "username_y": 0, "password_x": 0, "password_y": 0,
        "qr_x": 0, "qr_y": 0, "layout_json": layout,
    }
    return build_card_render_model(
        template,
        {"id": "915", "username": "card-915", "password": "Pw_9152"},
    )


# ════════════════════════════════════════════════════════════════════════
# (1) كل preset في المعرض يَحمل motif + يَنطبق على vertical
# ════════════════════════════════════════════════════════════════════════
class TestPresetMotifAssignment:

    def test_every_gallery_preset_has_icon_key(self):
        for key, preset in GALLERY_PRESETS.items():
            assert "icon" in preset, f"{key}: مفتاح icon مَفقود"
            assert preset["icon"], f"{key}: icon فارغ"

    def test_motif_keys_resolve(self):
        for key, preset in GALLERY_PRESETS.items():
            assert resolve_motif(preset["icon"])

    def test_vertical_motif_mapping_consistent(self):
        for key, (vertical, _style) in GALLERY_META.items():
            assert vertical in VERTICAL_TO_MOTIF


# ════════════════════════════════════════════════════════════════════════
# (2) Per-vertical motif SETS — كل قِطاع يَحمل عِدّة motifs مُتنَوّعة
# ════════════════════════════════════════════════════════════════════════
class TestVerticalSets:

    def test_cafe_set_has_at_least_5_motifs(self):
        assert len(cmp.VERTICAL_SETS["cafe"]) >= 5

    def test_clinic_set_has_at_least_5_motifs(self):
        assert len(cmp.VERTICAL_SETS["clinic"]) >= 5

    def test_every_vertical_has_at_least_3_motifs(self):
        for v, motifs in cmp.VERTICAL_SETS.items():
            assert len(motifs) >= 3, f"{v}: {len(motifs)} motifs فقط"

    def test_cafe_motifs_distinct_from_clinic(self):
        cafe_paths = cmp.build_tile_paths("cafe")
        clinic_paths = cmp.build_tile_paths("clinic")
        assert cafe_paths != clinic_paths


# ════════════════════════════════════════════════════════════════════════
# (3) Tile pattern composer
# ════════════════════════════════════════════════════════════════════════
class TestTilePatternComposer:

    def test_build_tile_paths_produces_svg_paths(self):
        paths = cmp.build_tile_paths("cafe")
        assert "<path" in paths
        assert "currentColor" in paths

    def test_build_pattern_svg_wraps_in_pattern_element(self):
        pat = cmp.build_pattern_svg("cafe", pattern_id="test-id")
        assert pat.startswith("<pattern")
        assert 'id="test-id"' in pat
        assert 'patternUnits="userSpaceOnUse"' in pat
        assert 'width="220.0"' in pat

    def test_pattern_size_under_6kb_per_vertical(self):
        for v in cmp.list_verticals():
            p = cmp.build_pattern_svg(v)
            assert len(p) < 6144, f"{v}: pattern {len(p)}B > 6KB"


# ════════════════════════════════════════════════════════════════════════
# (4) Card renderer يَنتج pattern_bg element
# ════════════════════════════════════════════════════════════════════════
class TestRendererProducesPatternBg:

    def test_clinic_preset_has_pattern_bg_element(self):
        model = _build_model("clinic_trust")
        ids = {e.get("id") for e in model["elements"]}
        kinds = {e.get("kind") for e in model["elements"]}
        assert "pattern_bg" in ids
        assert "pattern_bg" in kinds
        # العَناصر القَديمة لم تَعد افتراضيّة
        assert "watermark" not in ids
        assert "brand_icon" not in ids

    def test_cafe_pattern_uses_cafe_vertical(self):
        model = _build_model("cafe_mint")
        pat = next(e for e in model["elements"]
                    if e.get("id") == "pattern_bg")
        assert pat["vertical"] == "cafe"

    def test_clinic_pattern_uses_clinic_vertical(self):
        model = _build_model("clinic_trust")
        pat = next(e for e in model["elements"]
                    if e.get("id") == "pattern_bg")
        assert pat["vertical"] == "clinic"

    def test_gaming_pattern_uses_gaming_vertical(self):
        model = _build_model("gaming_neon")
        pat = next(e for e in model["elements"]
                    if e.get("id") == "pattern_bg")
        assert pat["vertical"] == "gaming"

    def test_default_pattern_opacity_is_15pct(self):
        """يونيو 2026 تَنقيح: 0.06 كان «بالكاد يُرى» — رَفعنا إلى 0.15
        كَنَمط خَلفيّة مَرئيّ دون مُنازَعَة الـQR/البَيانات (opaque
        فَوقها). clamp 0.30 يَبقى."""
        model = _build_model("clinic_trust")
        pat = next(e for e in model["elements"]
                    if e.get("id") == "pattern_bg")
        assert abs(pat["opacity"] - 0.15) < 0.001
        assert pat["opacity"] <= 0.30

    def test_opacity_clamped_at_30pct(self):
        layout = ops._template_layout({
            "design_preset": "cafe_mint",
            "watermark_opacity": "0.95",
        })
        assert layout["watermark_opacity"] <= 0.30


# ════════════════════════════════════════════════════════════════════════
# (5) Toggle on/off
# ════════════════════════════════════════════════════════════════════════
class TestPatternControl:

    def test_pattern_disabled_removes_element(self):
        layout = ops._template_layout({
            "design_preset": "clinic_trust",
            "watermark_enabled": "0",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        ids = {e.get("id") for e in model["elements"]}
        assert "pattern_bg" not in ids

    def test_opacity_zero_removes_pattern(self):
        layout = ops._template_layout({
            "design_preset": "cafe_mint",
            "watermark_enabled": "1",
            "watermark_opacity": "0",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        ids = {e.get("id") for e in model["elements"]}
        assert "pattern_bg" not in ids

    def test_brand_icon_opt_in_still_works(self):
        """toggle اختياريّ يَبقى — لمَن يُريد رَمز زاوية إضافيّ."""
        layout = ops._template_layout({
            "design_preset": "clinic_trust",
            "brand_icon_enabled": "1",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        ids = {e.get("id") for e in model["elements"]}
        assert "pattern_bg" in ids
        assert "brand_icon" in ids


# ════════════════════════════════════════════════════════════════════════
# (6) SVG output يَحوي <pattern> + يَتَكَرّر
# ════════════════════════════════════════════════════════════════════════
class TestSvgPatternOutput:

    def test_svg_contains_pattern_element_by_default(self):
        model = _build_model("clinic_trust")
        svg = render_card_svg(model)
        assert "<pattern" in svg
        assert 'class="card-pattern-bg"' in svg
        assert 'data-vertical="clinic"' in svg

    def test_svg_pattern_repeats_via_url_fill(self):
        model = _build_model("cafe_mint")
        svg = render_card_svg(model)
        m = re.search(r'<pattern id="([^"]+)"', svg)
        assert m
        pat_id = m.group(1)
        assert f'fill="url(#{pat_id})"' in svg

    def test_pattern_drawn_before_text(self):
        model = _build_model("cafe_mint")
        order_ids = [e.get("id") for e in model["elements"]]
        pat_idx = order_ids.index("pattern_bg")
        brand_idx = order_ids.index("brand")
        title_idx = order_ids.index("title")
        assert pat_idx < brand_idx
        assert pat_idx < title_idx


# ════════════════════════════════════════════════════════════════════════
# (7) حارس عَدم تَداخل title/brand/QR
# ════════════════════════════════════════════════════════════════════════
class TestNoOverlap:

    def _bbox(self, el: dict):
        kind = el.get("kind")
        if kind == "text":
            x = float(el["x"])
            y = float(el["y"])
            w = float(el.get("max_width") or 0)
            h = float(el["size"]) * 1.35
            return (x, y, x + w, y + h)
        if kind == "qr":
            x = float(el["x"])
            y = float(el["y"])
            s = float(el["size"])
            return (x, y, x + s, y + s)
        return (0, 0, 0, 0)

    @pytest.mark.parametrize("preset_key,engine", [
        ("clinic_trust", "ar_horizontal"),
        ("cafe_mint", "ar_horizontal"),
        ("gaming_neon", "ar_horizontal"),
        ("shop_bold", "ar_horizontal"),
        ("clinic_trust", "en_horizontal"),
        ("cafe_mint", "en_horizontal"),
    ])
    def test_title_does_not_overlap_qr_x_range(self, preset_key, engine):
        model = _build_model(preset_key, engine=engine)
        title_el = next(e for e in model["elements"]
                         if e.get("id") == "title")
        qr_el = next(e for e in model["elements"]
                      if e.get("id") == "qr")
        tbb = self._bbox(title_el)
        qbb = self._bbox(qr_el)
        tx = (tbb[0], tbb[2])
        qx = (qbb[0], qbb[2])
        assert not (tx[1] > qx[0] and qx[1] > tx[0]), (
            f"{preset_key} [{engine}]: title X {tx} overlaps QR X {qx}")


# ════════════════════════════════════════════════════════════════════════
# (8) PDF export smoke
# ════════════════════════════════════════════════════════════════════════
class TestPdfSmoke:

    def test_pdf_renders_card_with_pattern_bg(self):
        from io import BytesIO
        from reportlab.pdfgen import canvas as _canvas
        from app.radius.services.card_renderer import render_card_pdf

        buf = BytesIO()
        pdf = _canvas.Canvas(buf)
        model = _build_model("clinic_trust")
        render_card_pdf(pdf, model, form_name="test_card")
        pdf.showPage()
        pdf.save()
        assert buf.tell() > 100
