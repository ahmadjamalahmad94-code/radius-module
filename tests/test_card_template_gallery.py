# -*- coding: utf-8 -*-
"""مكتبة «القوالب الجاهزة» الموسّعة للكروت: التسجيل في المعرض، تصيير
كل قالب بحقول القسيمة المطلوبة، صحّة الرموز، تدفّق pattern_style من
القالب، وتصدير PDF عيّنة. شغّل الملف وحده."""
from __future__ import annotations

import io
import re

import pytest

from app.radius.services import operations as ops
from app.radius.services.card_template_gallery import GALLERY_META, GALLERY_PRESETS

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_REQUIRED_IDS = {"brand", "title", "user", "pass", "qr", "meta", "footer"}
_VERTICALS = {"cafe", "restaurant", "clinic", "shop", "isp", "hotel", "salon",
              "gym", "school", "events", "mosque", "gaming", "generic"}


def _build_model(preset_key: str):
    layout = ops._template_layout({"design_preset": preset_key})
    template = {
        "id": 1, "name": "t", "orientation": "portrait",
        "cards_per_row": 2, "cards_per_column": 5, "page_size": "A4",
        "font_size": 12, "color": "#111", "show_qr": True,
        "username_x": 0, "username_y": 0, "password_x": 0, "password_y": 0,
        "qr_x": 0, "qr_y": 0, "layout_json": layout,
    }
    from app.radius.services.card_renderer import build_card_render_model
    return build_card_render_model(
        template, {"id": "915", "username": "card-915", "password": "Pw_9152"})


# ── (1) التسجيل في المعرض ──
def test_gallery_presets_registered():
    assert len(GALLERY_PRESETS) >= 30, "المطلوب 30+ قالبًا جاهزًا جديدًا"
    for key in GALLERY_PRESETS:
        assert key in ops._PRINT_PRESETS, f"{key} غير مدموج في المعرض"
    # المعرض الكلّي اتسع
    assert len(ops._PRINT_PRESETS) >= 40
    listed = {p["key"] for p in ops._print_presets_list()}
    for key in GALLERY_PRESETS:
        assert key in listed, f"{key} لا يظهر في قائمة المعرض"


def test_new_keys_do_not_clobber_existing():
    # لا يدوس قالب جديد على قالب أساسي قائم (modern/dark/...).
    base = {"modern", "dark", "gold", "minimal", "telecom", "neon",
            "aurora", "fiber", "sunset", "matrix"}
    assert base.isdisjoint(set(GALLERY_PRESETS)), "تعارض مفاتيح مع القوالب الأساسية"


def test_every_vertical_covered():
    verts = {v for v, _s in GALLERY_META.values()}
    assert _VERTICALS.issubset(verts), f"أنواع ناقصة: {_VERTICALS - verts}"


# ── (2) صحّة الرموز ──
@pytest.mark.parametrize("key", sorted(GALLERY_PRESETS))
def test_preset_tokens_valid(key):
    p = GALLERY_PRESETS[key]
    for ck in ("gradient_start", "gradient_end", "accent_color",
               "text_color", "surface_color"):
        assert _HEX.match(p[ck]), f"{key}.{ck} لون غير صالح: {p[ck]}"
    assert p["qr_style"] in {"boxed", "rounded", "clean"}, key
    assert p["pattern_style"] in {"signal", "wave", "grid", "clean"}, key
    for tk in ("label", "brand_name", "card_title", "footer_text"):
        assert p[tk].strip(), f"{key}.{tk} فارغ"


def test_no_real_brand_names_leaked():
    """أسماء/علامات حقيقية ممنوعة (المحتوى مولّد أصليّ، الاسم placeholder
    عام قابل للتحرير)."""
    blob = " ".join(
        f"{p['brand_name']} {p['card_title']} {p['footer_text']}"
        for p in GALLERY_PRESETS.values()).lower()
    for brand in ("zain", "mobily", "stc", "ooredoo", "starbucks",
                  "mcdonald", "kfc", "زين", "موبايلي", "أورنج"):
        assert brand not in blob, f"اسم علامة مسرّب: {brand}"


# ── (3) تصيير حقول القسيمة لكل قالب ──
@pytest.mark.parametrize("key", sorted(GALLERY_PRESETS))
def test_preset_renders_required_voucher_fields(key):
    model = _build_model(key)
    ids = {e.get("id") for e in model["elements"]}
    assert _REQUIRED_IDS.issubset(ids), f"{key}: حقول ناقصة {_REQUIRED_IDS - ids}"
    by_id = {e["id"]: e for e in model["elements"] if e.get("id")}
    assert by_id["user"]["value"] == "card-915"
    assert by_id["pass"]["value"] == "Pw_9152"
    assert by_id["qr"].get("payload"), f"{key}: QR بلا payload"


# ── (4) pattern_style يتدفّق من القالب ──
def test_pattern_style_flows_from_preset():
    # hotel_lux معرّف بنمط grid، وcafe_warm بنمط wave.
    assert ops._template_layout({"design_preset": "hotel_lux"})["pattern_style"] == "grid"
    assert ops._template_layout({"design_preset": "cafe_warm"})["pattern_style"] == "wave"
    # القوالب الأساسية القديمة تبقى على الافتراضي signal (لا انحدار).
    assert ops._template_layout({"design_preset": "modern"})["pattern_style"] == "signal"


# ── (5) تصدير PDF عيّنة ──
def test_pdf_export_smoke():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.radius.services.card_renderer import (
        place_card_form_uniform, render_card_pdf,
    )
    model = _build_model("hotel_lux")
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    render_card_pdf(pdf, model, form_name="card0")
    place_card_form_uniform(pdf, model, form_name="card0",
                            slot_x=40, slot_y=500, slot_width=300, slot_height=190)
    pdf.showPage()
    pdf.save()
    data = buf.getvalue()
    assert data[:4] == b"%PDF", "ناتج ليس PDF"
    assert len(data) > 1500, "PDF صغير جدًا — التصيير فشل غالبًا"


def test_pdf_export_smoke_arabic_and_colorful():
    """عيّنة ثانية بنمط مختلف + نصّ عربي للتأكد أن التصدير لا ينهار."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.radius.services.card_renderer import (
        place_card_form_uniform, render_card_pdf,
    )
    for key in ("cafe_warm", "gaming_neon", "mosque_serene"):
        model = _build_model(key)
        buf = io.BytesIO()
        pdf = canvas.Canvas(buf, pagesize=A4)
        render_card_pdf(pdf, model, form_name="c")
        place_card_form_uniform(pdf, model, form_name="c",
                                slot_x=40, slot_y=500, slot_width=300, slot_height=190)
        pdf.showPage()
        pdf.save()
        assert buf.getvalue()[:4] == b"%PDF", f"{key}: PDF فشل"
