"""مقاسات خط اليوزر/الباس بالنقاط الطباعية (طلب المالك: «مثل الوورد 12، 14»).

`font_size_unit='pt'`: القيم المخزّنة نقاط حقيقية تُحوَّل لوحدات الكانفس
بمعامل (عرض الكانفس ÷ عرض البطاقة بالنقاط) — فبطاقة مطبوعة بمقاسها
المليمتري يظهر خطها بحجم النقاط الفعلي. القوالب القديمة (بلا العلم)
تبقى بوحدات الكانفس حرفيًا — رندرها لا يتغير.
"""
from __future__ import annotations

import pytest

MM_TO_PT = 72.0 / 25.4


def _template(layout: dict) -> dict:
    base = {
        "card_width_mm": 85.6, "card_height_mm": 54,
        "background_style": "preset", "design_preset": "modern",
        "hotspot_address": "hotspot.local",
    }
    base.update(layout)
    return {
        "id": 1, "name": "T", "orientation": "portrait",
        "cards_per_row": 2, "cards_per_column": 5, "page_size": "A4",
        "font_size": 12, "color": "#1f2937", "show_qr": True,
        "username_x": 0, "username_y": 0, "password_x": 0, "password_y": 0,
        "qr_x": 0, "qr_y": 0, "layout_json": base,
    }


def _user_pill(model):
    return next(e for e in model["elements"] if e.get("id") == "user")


def test_pt_unit_converts_to_canvas_units():
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _template({"font_size_unit": "pt", "username_font_size": 14.0}),
        {"id": 1, "username": "u1", "password": "p1"})
    factor = 1000.0 / (85.6 * MM_TO_PT)          # ≈ 4.12
    assert _user_pill(model)["value_font_size"] == pytest.approx(14.0 * factor, rel=1e-3)


def test_legacy_canvas_units_unchanged_without_flag():
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _template({"username_font_size": 40.0}),
        {"id": 1, "username": "u1", "password": "p1"})
    assert _user_pill(model)["value_font_size"] == pytest.approx(40.0)


def test_pt_unit_on_vertical_engine_uses_oriented_width():
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _template({
            "font_size_unit": "pt", "username_font_size": 12.0,
            "render_engine": "ar_vertical", "card_orientation": "vertical",
            "card_width_mm": 54, "card_height_mm": 85.6,
        }),
        {"id": 1, "username": "u1", "password": "p1"})
    factor = 600.0 / (54.0 * MM_TO_PT)           # الكانفس العمودي 600 عرضًا
    assert _user_pill(model)["value_font_size"] == pytest.approx(12.0 * factor, rel=1e-3)


def test_template_layout_persists_unit():
    from app.radius.services.operations import _template_layout

    assert _template_layout({"font_size_unit": "pt"})["font_size_unit"] == "pt"
    assert _template_layout({})["font_size_unit"] == ""
    assert _template_layout({"font_size_unit": "px"})["font_size_unit"] == ""


def test_payload_reads_unit_from_form():
    import os
    os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    app = create_app()
    from app.radius.routes.print_templates import _payload

    with app.test_request_context(
            method="POST",
            data={"name": "T", "font_size_unit": "pt",
                  "username_font_size": "14"}):
        layout = _payload()["layout"]
    assert layout["font_size_unit"] == "pt"
    assert layout["username_font_size"] == pytest.approx(14.0)
