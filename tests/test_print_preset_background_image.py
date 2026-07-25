"""«خلفية من صورة» داخل «تصميم من النظام» (طلب المالك 2026-07-25).

كان منتقي رفع الصورة مخفيًا كليًا في وضع تصميم النظام — الصورة كانت
إمّا «كل البطاقة» (وضع صورة مرفوعة) أو لا شيء. الميزة: علم صريح
`preset_background_image` يجعل الصورة طبقة خلفية تُرسم فوق التدرّج
وتحت الزخرفة والبيانات، مع بقاء كل طبقات تصميم النظام.

الثوابت المحمية:
  • العلم مفعّل ⇒ الصورة في نموذج الرندر مع بقاء source='preset'
    والزخرفة، وكل عناصر التصميم (لا اختزال لعناصر الصورة الثلاثة).
  • العلم غائب/موقوف ⇒ صورة محفوظة قديمة لا تظهر (رندر القوالب
    القديمة لا يتغيّر).
  • ترتيب SVG: تدرّج ثم صورة (ثم الزخرفة فوقها).
  • الحفظ (_template_layout) والنموذج (_payload) يحملان العلم؛ رفع
    صورة في وضع النظام مع العلم لا يقلب الوضع إلى image.
"""
from __future__ import annotations

import pytest

# 1×1 شفاف PNG.
DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


def _template(layout: dict) -> dict:
    base = {
        "card_width_mm": 85.6, "card_height_mm": 54,
        "background_style": "preset",
        "design_preset": "modern",
        "pattern_style": "signal",
        "brand_name": "هوب راديوس",
        "card_title": "بطاقة إنترنت",
        "hotspot_address": "hotspot.local",
        "show_username": True, "show_password": True, "show_qr": True,
    }
    base.update(layout)
    return {
        "id": 1, "name": "T", "orientation": "portrait",
        "cards_per_row": 2, "cards_per_column": 5, "page_size": "A4",
        "font_size": 12, "color": "#1f2937", "show_qr": True,
        "username_x": 0, "username_y": 0, "password_x": 0, "password_y": 0,
        "qr_x": 0, "qr_y": 0, "layout_json": base,
    }


def test_flag_on_layers_image_under_system_design():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    model = build_card_render_model(
        _template({
            "background_image_data_url": DATA_URL,
            "preset_background_image": True,
            "image_opacity": 0.6,
        }),
        {"id": 1, "username": "u1", "password": "p1"},
    )
    bg = model["background"]
    assert bg["source"] == "preset", bg
    assert bg["image_data_url"].startswith("data:image/png;base64,")
    assert abs(bg["image_opacity"] - 0.6) < 1e-6
    assert bg["pattern"] == "signal"  # الزخرفة تبقى (لا تُقلب clean)
    # كل طبقات تصميم النظام باقية — لا اختزال «الصورة الجاهزة» الثلاثي.
    ids = {e["id"] for e in model["elements"]}
    assert ids != {"user", "pass", "qr"}

    svg = render_card_svg(model)
    # الترتيب: التدرّج أولًا ثم الصورة فوقه (والزخرفة بعدها).
    grad_pos = svg.index('fill="url(#')
    img_pos = svg.index("data:image/png;base64,")
    assert grad_pos < img_pos
    assert 'opacity="0.60"' in svg


def test_flag_off_keeps_legacy_render():
    """صورة محفوظة من وضع سابق لا تظهر في وضع النظام بلا العلم."""
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _template({"background_image_data_url": DATA_URL}),
        {"id": 1, "username": "u1", "password": "p1"},
    )
    assert model["background"]["source"] == "preset"
    assert model["background"]["image_data_url"] == ""


def test_uploaded_image_mode_unchanged():
    """وضع «صورة مرفوعة» كما هو: الصورة كامل البطاقة والعناصر الثلاثة."""
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _template({
            "background_style": "image",
            "background_image_data_url": DATA_URL,
        }),
        {"id": 1, "username": "u1", "password": "p1"},
    )
    assert model["background"]["source"] == "image"
    assert {e["id"] for e in model["elements"]} == {"user", "pass", "qr"}


def test_template_layout_persists_flag(app):
    from app.radius.services.operations import _template_layout

    on = _template_layout({"preset_background_image": "1"})
    off = _template_layout({})
    assert on["preset_background_image"] is True
    assert off["preset_background_image"] is False


def test_payload_upload_in_preset_mode_keeps_preset(app):
    """رفع صورة مع العلم في وضع النظام لا يقلب background_style إلى image."""
    from app.radius.routes.print_templates import _payload

    form = {
        "name": "T",
        "background_style": "preset",
        "preset_background_image": "1",
        "background_image_data_url": DATA_URL,
        "background_image_name": "bg.png",
    }
    with app.test_request_context(method="POST", data=form):
        layout = _payload()["layout"]
    assert layout["background_style"] == "preset"
    assert layout["preset_background_image"] is True
    assert layout["background_image_data_url"].startswith("data:image/")

    # بلا العلم: السلوك القديم — الرفع يقلب الوضع إلى image.
    form_off = {**form}
    del form_off["preset_background_image"]
    with app.test_request_context(method="POST", data=form_off):
        layout_off = _payload()["layout"]
    assert layout_off["background_style"] == "image"


def test_faded_png_helper_returns_png():
    from app.radius.services.card_renderer import _faded_rgba_png

    import base64
    raw = base64.b64decode(DATA_URL.split(";base64,", 1)[1])
    out = _faded_rgba_png(raw, 0.5)
    assert out is not None and out[:8] == b"\x89PNG\r\n\x1a\n"
