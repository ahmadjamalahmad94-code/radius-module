# -*- coding: utf-8 -*-
"""feat/card-template-icons — اختبارات للرَموز القِطاعيّة + العَلامة المائيّة
+ حارس عَدم تَداخل عُنصري العنوان والـQR.

السياق (يونيو 2026، طلب المالك): القوالب الـ44 كانت تَتَمايز بالألوان
فقط. صار كلّ preset يَحمل motif مُميِّز يُرسم بصورتين:
  - icon: رَمز صَغير بِجانب الـbrand
  - watermark: رَمز كَبير بشَفافيّة مُنخفضة خَلف المحتوى

وأُصلِح انحدار حَيّ: «دخول الإنترنت» (title) كان يُغطّي الـQR + الـbrand
على بَعض الـpresets — heading_width صار يَضِيق عندما يَكون الـQR ظاهرًا.

شغّل وحده (عزل الاختبارات لكل ملف)."""
from __future__ import annotations

import re

import pytest

from app.radius.services import operations as ops
from app.radius.services.card_renderer import build_card_render_model, render_card_svg
from app.radius.services.card_motifs import (
    motif_svg, resolve_motif, VERTICAL_TO_MOTIF,
)
from app.radius.services.card_template_gallery import GALLERY_META, GALLERY_PRESETS


def _build_layout(key: str, *, engine: str = "ar_horizontal") -> dict:
    return ops._template_layout({
        "design_preset": key, "render_engine": engine,
    })


def _build_model(key: str, *, engine: str = "ar_horizontal"):
    layout = _build_layout(key, engine=engine)
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
# (1) كل preset في المعرض يَحمل motif مُسجَّل
# ════════════════════════════════════════════════════════════════════════
class TestPresetMotifAssignment:

    def test_every_gallery_preset_has_icon_key(self):
        """كل preset في GALLERY_PRESETS يَحمل مفتاح ``icon`` غير فارغ."""
        for key, preset in GALLERY_PRESETS.items():
            assert "icon" in preset, f"{key}: مفتاح icon مَفقود"
            assert preset["icon"], f"{key}: icon فارغ"

    def test_motif_keys_resolve(self):
        """كل icon المُسجَّل يَنطبق على motif سَليم (ليس fallback أعمى)."""
        for key, preset in GALLERY_PRESETS.items():
            motif = preset["icon"]
            resolved = resolve_motif(motif)
            assert resolved, f"{key}: motif {motif!r} لا يَنطبق"

    def test_vertical_motif_mapping_consistent(self):
        """vertical→motif: كل القِطاعات المُستعملة في GALLERY_META مَوجودة في
        VERTICAL_TO_MOTIF."""
        for key, (vertical, _style) in GALLERY_META.items():
            assert vertical in VERTICAL_TO_MOTIF, \
                f"{key}: vertical={vertical} غَير مُسجَّل في VERTICAL_TO_MOTIF"

    def test_each_vertical_uses_its_own_motif(self):
        """قَطاعات مختلفة → motifs مختلفة. مَنع «كلها واي‑فاي»."""
        used_motifs: set[str] = set()
        for _key, preset in GALLERY_PRESETS.items():
            used_motifs.add(resolve_motif(preset["icon"]))
        # نَتوقّع على الأقلّ 10 motifs مُختلفة بين الـ34 قَالبًا الجَالاريّة
        assert len(used_motifs) >= 10, \
            f"تَنوّع motifs ضَعيف: {used_motifs}"


# ════════════════════════════════════════════════════════════════════════
# (2) Render: كل بطاقة تَحوي عُنصرَي icon + watermark
# ════════════════════════════════════════════════════════════════════════
class TestRendererIncludesMotifs:

    def test_clinic_preset_has_watermark_but_no_icon_by_default(self):
        """تَنقيح المالك (يونيو 2026): الافتراضيّ = watermark فقط، بلا
        brand_icon بارز («دفش ومبالغ فيه»)."""
        model = _build_model("clinic_trust")
        ids = {e.get("id") for e in model["elements"]}
        assert "watermark" in ids, "watermark عَنصر مَفقود (افتراضيّ on)"
        assert "brand_icon" not in ids, \
            "brand_icon يَجب أن يَكون مَوقوفًا افتراضيًّا"

    def test_brand_icon_enabled_opts_in(self):
        """toggle اختياريّ: brand_icon_enabled=true يُظهر الرَمز."""
        layout = ops._template_layout({
            "design_preset": "clinic_trust",
            "brand_icon_enabled": "1",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        ids = {e.get("id") for e in model["elements"]}
        assert "brand_icon" in ids
        icon_el = next(e for e in model["elements"]
                        if e.get("id") == "brand_icon")
        assert icon_el["motif"] == "medical"

    def test_cafe_watermark_uses_coffee_motif(self):
        model = _build_model("cafe_mint")
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        assert wm_el["motif"] == "coffee"

    def test_clinic_watermark_uses_medical_motif(self):
        model = _build_model("clinic_trust")
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        assert wm_el["motif"] == "medical"

    def test_gaming_watermark_uses_gamepad_motif(self):
        model = _build_model("gaming_neon")
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        assert wm_el["motif"] == "gamepad"

    def test_watermark_default_opacity_is_subtle(self):
        """تَنقيح المالك: شَفافيّة افتراضيّة ~4٪ (كانت 10٪) — هَمس
        بَصريّ لا يَطغى على الـQR/البَيانات."""
        model = _build_model("clinic_trust")
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        assert 0 < wm_el["opacity"] <= 0.06, (
            f"opacity {wm_el['opacity']} ليست هَامِسة (يَجب ≤6٪)")

    def test_watermark_opacity_caps_at_30pct(self):
        """الحَدّ الأقصى للشَفافيّة 0.30 (كان 0.40 سابقًا — قُلِّص لمَنع
        فَوضى الطباعة)."""
        layout = ops._template_layout({
            "design_preset": "clinic_trust",
            "watermark_opacity": "0.95",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        assert wm_el["opacity"] <= 0.30

    def test_brand_icon_uses_same_motif_as_watermark_when_enabled(self):
        """تَناسُق بَصري عند تَفعيل الرَمز: نَفس motif الـwatermark."""
        for key in ("cafe_mint", "clinic_trust", "gaming_neon", "shop_bold"):
            layout = ops._template_layout({
                "design_preset": key, "brand_icon_enabled": "1",
            })
            template = {"id": 1, "name": "t", "orientation": "portrait",
                         "layout_json": layout}
            model = build_card_render_model(template,
                {"username": "u", "password": "p", "id": 1})
            icon_el = next(e for e in model["elements"]
                            if e.get("id") == "brand_icon")
            wm_el = next(e for e in model["elements"]
                          if e.get("id") == "watermark")
            assert icon_el["motif"] == wm_el["motif"], \
                f"{key}: icon={icon_el['motif']} ≠ watermark={wm_el['motif']}"


# ════════════════════════════════════════════════════════════════════════
# (3) watermark on/off + opacity من «الزخرفة والخلفية»
# ════════════════════════════════════════════════════════════════════════
class TestWatermarkControl:

    def test_watermark_disabled_removes_element(self):
        layout = ops._template_layout({
            "design_preset": "clinic_trust",
            "watermark_enabled": "0",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        ids = {e.get("id") for e in model["elements"]}
        assert "watermark" not in ids, "العَلامة المائيّة لم تُحجَب"
        # brand_icon افتراضيًّا مَوقوف — لا يَظهر هنا أيضًا.
        assert "brand_icon" not in ids

    def test_brand_icon_disabled_by_default_even_with_watermark(self):
        """الانحدار الحَيّ (يونيو 2026): الرَمز الصَغير مَوقوف افتراضيًّا
        حَتّى مَع تَفعيل الـwatermark."""
        layout = ops._template_layout({"design_preset": "cafe_mint"})
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template,
            {"username": "u", "password": "p", "id": 1})
        ids = {e.get("id") for e in model["elements"]}
        assert "watermark" in ids
        assert "brand_icon" not in ids

    def test_watermark_opacity_override(self):
        layout = ops._template_layout({
            "design_preset": "cafe_mint",
            "watermark_enabled": "1",
            "watermark_opacity": "0.10",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        assert abs(wm_el["opacity"] - 0.10) < 0.01

    def test_watermark_opacity_clamped_at_30pct(self):
        """قيمة > 0.30 تَتقصّى إلى 0.30 (الحَدّ الأقصى الجَديد بَعد
        تَنقيح المالك — كان 0.40)."""
        layout = ops._template_layout({
            "design_preset": "cafe_mint",
            "watermark_opacity": "0.95",
        })
        assert layout["watermark_opacity"] <= 0.30

    def test_default_watermark_opacity_is_4pct(self):
        """الافتراضيّ الجَديد 0.04 (كان 0.10) — هَمس بَصريّ."""
        layout = ops._template_layout({"design_preset": "cafe_mint"})
        assert abs(layout["watermark_opacity"] - 0.04) < 0.001

    def test_icon_motif_override_per_layout(self):
        """يُمكن للمُصمِّم تَجاوز motif الـpreset عبر حقل ``icon`` صريح —
        يَنطبق على الـwatermark (والـbrand_icon إذا فُعِّل)."""
        layout = ops._template_layout({
            "design_preset": "cafe_mint",  # افتراضي coffee
            "icon": "dumbbell",
            "brand_icon_enabled": "1",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template, {"username": "u",
                                                     "password": "p", "id": 1})
        wm_el = next(e for e in model["elements"]
                      if e.get("id") == "watermark")
        icon_el = next(e for e in model["elements"]
                        if e.get("id") == "brand_icon")
        assert wm_el["motif"] == "dumbbell"
        assert icon_el["motif"] == "dumbbell"


# ════════════════════════════════════════════════════════════════════════
# (4) SVG output يَحوي العَلامات الـCSS الصَحيحة + الـmotif
# ════════════════════════════════════════════════════════════════════════
class TestSvgOutput:

    def test_svg_contains_watermark_class_by_default(self):
        model = _build_model("clinic_trust")
        svg = render_card_svg(model)
        assert 'class="card-watermark"' in svg
        assert 'data-motif="medical"' in svg

    def test_svg_omits_icon_class_by_default(self):
        """الافتراضيّ: لا card-icon (الرَمز الصَغير مَوقوف)."""
        model = _build_model("clinic_trust")
        svg = render_card_svg(model)
        assert 'class="card-icon"' not in svg

    def test_svg_contains_icon_class_when_brand_icon_enabled(self):
        layout = ops._template_layout({
            "design_preset": "clinic_trust",
            "brand_icon_enabled": "1",
        })
        template = {"id": 1, "name": "t", "orientation": "portrait",
                     "layout_json": layout}
        model = build_card_render_model(template,
            {"username": "u", "password": "p", "id": 1})
        svg = render_card_svg(model)
        assert 'class="card-icon"' in svg

    def test_watermark_drawn_before_text(self):
        """ترتيب العَناصر: watermark يَجب أن يَجلس فَوق الخَلفيّة لكن
        أسفل النَصّ — أي قَبل عَناصر النَصّ في الـSVG."""
        model = _build_model("cafe_mint")
        order_ids = [e.get("id") for e in model["elements"]]
        wm_idx = order_ids.index("watermark")
        brand_idx = order_ids.index("brand")
        title_idx = order_ids.index("title")
        assert wm_idx < brand_idx, "watermark بَعد brand → سيُغطّيه"
        assert wm_idx < title_idx, "watermark بَعد title → سيُغطّيه"


# ════════════════════════════════════════════════════════════════════════
# (5) حارس عَدم تَداخل title/brand/QR (الانحدار الحَيّ)
# ════════════════════════════════════════════════════════════════════════
class TestNoOverlap:
    """الانحدار الحَيّ: «دخول الإنترنت» (title) كان يُغطّي الـQR والـbrand
    «عيادتك». السَبب: heading_width = 0.55 افتراضيًّا حتى عند ظُهور الـQR،
    والـbox الكامل (للـclip) يَمتدّ فَوق منطقة الـQR. الإصلاح: heading
    _width يَضِيق إلى الحُدود الآمنة عند ظُهور الـQR."""

    def _bbox(self, el: dict, model: dict) -> tuple[float, float, float, float]:
        """يُرجع (x_min, y_min, x_max, y_max) لعُنصر."""
        kind = el.get("kind")
        if kind == "text":
            x = float(el["x"])
            y = float(el["y"])
            w = float(el.get("max_width") or 0)
            h = float(el["size"]) * 1.35  # تَقدير ارتفاع السَطر
            direction = el.get("direction") or "ltr"
            # في RTL، الرَسم انكوريد عند x+w (الحَافة اليُمنى)
            # لكن النَصّ يَمتدّ من x إلى x+w (هذا هو الـclip box)
            return (x, y, x + w, y + h)
        if kind == "qr":
            x = float(el["x"])
            y = float(el["y"])
            s = float(el["size"])
            return (x, y, x + s, y + s)
        if kind == "pill":
            x = float(el["x"])
            y = float(el["y"])
            w = float(el["width"])
            h = float(el["height"])
            return (x, y, x + w, y + h)
        return (0, 0, 0, 0)

    def _overlap(self, a, b) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)

    @pytest.mark.parametrize("preset_key,engine", [
        ("clinic_trust", "ar_horizontal"),
        ("cafe_mint", "ar_horizontal"),
        ("gaming_neon", "ar_horizontal"),
        ("shop_bold", "ar_horizontal"),
        ("resto_appetite", "ar_horizontal"),
        ("clinic_trust", "en_horizontal"),
        ("cafe_mint", "en_horizontal"),
    ])
    def test_title_does_not_overlap_qr(self, preset_key, engine):
        model = _build_model(preset_key, engine=engine)
        title_el = next(e for e in model["elements"]
                         if e.get("id") == "title")
        qr_el = next(e for e in model["elements"]
                      if e.get("id") == "qr")
        # «النَصّ الفعليّ» قَد يَكون أقصر من الـmax_width، لكن لمَنع التَصادم
        # بَصريًّا بقاطعيّة: مُربّع الـclip للـtitle يَجب ألا يَتداخل أُفقيًّا
        # مع مُربّع الـQR (نَترك تَداخلًا عَموديًّا — title فَوق + QR أسفل
        # غالبًا، لكن عَموديًّا قَد يَلتقيان حسب الإحداثيّات).
        title_bbox = self._bbox(title_el, model)
        qr_bbox = self._bbox(qr_el, model)
        # نَفحص التَداخل الأُفقيّ صَراحةً (Y غير شَرط — الـQR يَمتدّ عَموديًّا
        # وheading_width الجَديد يَضمن عَدم تَقاطع X)
        title_x_range = (title_bbox[0], title_bbox[2])
        qr_x_range = (qr_bbox[0], qr_bbox[2])
        # خَطّ X: title لا يَدخل في حُدود QR
        x_overlap = not (title_x_range[1] <= qr_x_range[0]
                          or qr_x_range[1] <= title_x_range[0])
        assert not x_overlap, (
            f"{preset_key} [{engine}]: title X range {title_x_range} "
            f"يَتداخل مع QR X range {qr_x_range}")

    @pytest.mark.parametrize("preset_key", [
        "clinic_trust", "cafe_mint", "gaming_neon", "shop_bold",
        "hotel_lux", "salon_glam",
    ])
    def test_brand_does_not_overlap_qr(self, preset_key):
        model = _build_model(preset_key, engine="ar_horizontal")
        brand_el = next(e for e in model["elements"]
                         if e.get("id") == "brand")
        qr_el = next(e for e in model["elements"]
                      if e.get("id") == "qr")
        brand_bbox = self._bbox(brand_el, model)
        qr_bbox = self._bbox(qr_el, model)
        brand_x_range = (brand_bbox[0], brand_bbox[2])
        qr_x_range = (qr_bbox[0], qr_bbox[2])
        x_overlap = not (brand_x_range[1] <= qr_x_range[0]
                          or qr_x_range[1] <= brand_x_range[0])
        assert not x_overlap, (
            f"{preset_key}: brand X range {brand_x_range} "
            f"يَتداخل مع QR X range {qr_x_range}")


# ════════════════════════════════════════════════════════════════════════
# (6) PDF export smoke — لا يَكسر مع icon + watermark
# ════════════════════════════════════════════════════════════════════════
class TestPdfSmoke:

    def test_pdf_renders_card_with_icon_and_watermark(self):
        """تَصدير PDF لا يَرمي على عُنصر icon أو watermark."""
        from io import BytesIO
        from reportlab.pdfgen import canvas as _canvas
        from app.radius.services.card_renderer import render_card_pdf

        buf = BytesIO()
        pdf = _canvas.Canvas(buf)
        model = _build_model("clinic_trust")
        render_card_pdf(pdf, model, form_name="test_card")
        pdf.showPage()
        pdf.save()
        assert buf.tell() > 100, "PDF يَجب أن يَحوي بَيانات"
