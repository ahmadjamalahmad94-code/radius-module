"""ملاءمة صورة الخلفية (شكوى «الصورة مقصوصة» — client1، 2026-07-25).

الصورة المرفوعة كانت تُقصّ دائمًا قصًّا مركزيًا لنسبة البطاقة (cover) —
مؤلم لمن يرفع تصميم بطاقة جاهزًا نسبته لا تطابق النسبة. الآن حقل
`image_fit`: cover (الافتراضي التاريخي) / contain (كاملة بلا قصّ) /
stretch (تمديد). يجب أن يتطابق سلوك SVG (preserveAspectRatio) مع
هندسة PDF (_contain_rect).
"""
from __future__ import annotations

import pytest

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
        "background_style": "image",
        "background_image_data_url": DATA_URL,
        "design_preset": "modern",
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


def _svg_for(fit: str | None) -> str:
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    layout = {} if fit is None else {"image_fit": fit}
    model = build_card_render_model(
        _template(layout), {"id": 1, "username": "u1", "password": "p1"})
    return render_card_svg(model)


def test_default_stays_cover_slice():
    assert 'preserveAspectRatio="xMidYMid slice"' in _svg_for(None)


def test_contain_uses_meet():
    assert 'preserveAspectRatio="xMidYMid meet"' in _svg_for("contain")


def test_stretch_uses_none():
    assert 'preserveAspectRatio="none"' in _svg_for("stretch")


def test_invalid_fit_falls_back_to_cover():
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _template({"image_fit": "diagonal"}),
        {"id": 1, "username": "u1", "password": "p1"})
    assert model["background"]["image_fit"] == "cover"


def test_contain_rect_geometry():
    from app.radius.services.card_renderer import _contain_rect

    # صورة عريضة 2:1 داخل إطار مربع 100×100 → شريطان أفقيان.
    x, y, w, h = _contain_rect(200, 100, 0, 0, 100, 100)
    assert (x, y, w, h) == (0, 25.0, 100.0, 50.0)
    # صورة طويلة 1:2 داخل نفس الإطار → شريطان جانبيان.
    x, y, w, h = _contain_rect(100, 200, 0, 0, 100, 100)
    assert (x, y, w, h) == (25.0, 0, 50.0, 100.0)
    # أبعاد فاسدة → الإطار كما هو (سقوط آمن).
    assert _contain_rect(0, 0, 1, 2, 30, 40) == (1, 2, 30, 40)


def test_payload_and_template_layout_carry_fit(app):
    from app.radius.routes.print_templates import _payload
    from app.radius.services.operations import _template_layout

    with app.test_request_context(
            method="POST",
            data={"name": "T", "background_style": "image",
                  "background_image_data_url": DATA_URL, "image_fit": "contain"}):
        assert _payload()["layout"]["image_fit"] == "contain"

    assert _template_layout({"image_fit": "contain"})["image_fit"] == "contain"
    assert _template_layout({})["image_fit"] == "cover"
    assert _template_layout({"image_fit": "weird"})["image_fit"] == "cover"
