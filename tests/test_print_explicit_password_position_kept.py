"""شكوى Fadi Net 2026-10-01: «يسحب كلمةَ المرور لفوق، يحفظ، فترجع لتحت».

الرصُّ التلقائيّ (F9) كان يدفع حبّةَ الباس تحت حبّةِ اليوزر بارتفاعها الكامل
(حتى وهي شفّافة فوق خلفيّةِ صورةٍ فيها المربّعان مرسومان)، فكلُّ موضعٍ بين
~18 و28mm كان يُطبع عند 28mm — والموضعُ فوق اليوزر كان يُرفع لفوق.
الموضعُ الذي يضعه المشغّلُ يُطبع كما هو؛ وحمايةُ سطرَي الرابط/التذييل
للخطوطِ الضخمة تبقى (``test_stress_fix_print``).

شغّلْ هذا الملفَّ وحدَه."""
from __future__ import annotations

import json

import pytest

from app.radius.services.card_renderer import build_card_render_model

# قالبُ فادي الحقيقيّ (card_print_templates id 2) مختصرًا لما يؤثّر في الموضع
_LAYOUT = {
    "card_width_mm": 85.6, "card_height_mm": 54.0,
    "render_engine": "ar_horizontal", "card_orientation": "horizontal",
    "username_font_size": 14.0, "password_font_size": 14.0,
    "credential_label_font_size": 0.0, "font_size_unit": "pt",
    "qr_size_pct": 20.0, "background_style": "image",
    "text_direction": "rtl", "show_qr": True, "show_username": True,
    "show_password": True, "show_hotspot": True, "show_serial": True,
    "credential_background_enabled": False,
}


def _template(password_y: float) -> dict:
    return {
        "orientation": "portrait", "show_qr": 1, "font_size": 12, "color": "#ffffff",
        "username_x": 13.7, "username_y": 17.7,
        "password_x": 18.1, "password_y": password_y,
        "qr_x": 64.7, "qr_y": 32.9,
        "layout_json": json.dumps(_LAYOUT),
    }


def _pills(password_y: float) -> tuple[dict, dict, float]:
    m = build_card_render_model(_template(password_y),
                                {"username": "12345678", "password": "8765"})
    pills = [e for e in m["elements"] if e.get("kind") == "pill"]
    user = next(p for p in pills if not p.get("is_password"))
    pw = next(p for p in pills if p.get("is_password"))
    px_per_mm = user["y"] / 17.7
    return user, pw, px_per_mm


@pytest.mark.parametrize("password_y", [24.9, 22.0, 20.0, 18.0, 15.0, 10.0, 30.0])
def test_operator_password_position_is_printed_where_placed(password_y):
    user, pw, k = _pills(password_y)
    assert pw["y"] / k == pytest.approx(password_y, abs=0.05), \
        "كلمةُ المرور لم تُطبع حيث وضعها المشغّل"
    assert user["y"] / k == pytest.approx(17.7, abs=0.05), "اليوزر تحرّك"
