"""رقم الكرت لا يُبتر أبدًا (شكوى client1 2026-07-27: «رقم كرت نصه طاير»).

تكبير خط اليوزر/الباس كان: في تصدير PDF يبتر القيمة بـ«…» (بطاقة برقم
ناقص لا قيمة لها)، وفي معاينة SVG يقصّها clipPath بصمت. الآن يُصغَّر
حجم الخط تلقائيًّا حتى تتسع القيمة كاملة (أرضية 4pt) — بنفس مقياس
Helvetica-Bold في المسارين فتتطابق المعاينة مع الملف.
"""
from __future__ import annotations

import re
import zlib
from io import BytesIO

LONG_USER = "1733232599887"
# خط كبير يجعل القيمة (~723pt بخط Helvetica-Bold) أعرض من الحبة (~410pt
# داخليًّا) حتمًا — فيثبت أن التصغير هو ما أبقاها كاملة.
BIG_FONT = 100.0


def _template(username_font: float):
    return {
        "id": 1, "name": "T", "orientation": "portrait",
        "cards_per_row": 2, "cards_per_column": 5, "page_size": "A4",
        "font_size": 12, "color": "#1f2937", "show_qr": True,
        "username_x": 0, "username_y": 0, "password_x": 0, "password_y": 0,
        "qr_x": 0, "qr_y": 0,
        "layout_json": {
            "card_width_mm": 85.6, "card_height_mm": 54,
            "background_style": "preset", "design_preset": "modern",
            "hotspot_address": "hotspot.local",
            "username_font_size": username_font,
        },
    }


def test_svg_shrinks_font_instead_of_clipping():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    model = build_card_render_model(
        _template(BIG_FONT), {"id": 1, "username": LONG_USER, "password": "12345"})
    svg = render_card_svg(model)
    # القيمة كاملة في المعاينة — لا بتر ولا «…».
    assert LONG_USER in svg
    assert "…" not in svg
    # الخط لا يتجاوز المطلوب أبدًا — وقد يساويه الآن لأن الحبة تتوسع
    # مع الخط (التصغير بقي شبكة أمان عند بلوغ سقف عرض الكانفس).
    pill_part = svg[svg.index(LONG_USER) - 600: svg.index(LONG_USER)]
    m = re.findall(r'font-size="([0-9.]+)"', pill_part)
    assert m and float(m[-1]) <= BIG_FONT


def _pdf_decoded_text(pdf_bytes: bytes) -> bytes:
    """يفك تدفقات PDF (ASCII85/Flate) ليُفتَّش عن النص المرسوم فعلًا."""
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.DOTALL):
        payload = m.group(1)
        if payload.rstrip().endswith(b"~>"):
            try:
                import base64
                payload = base64.a85decode(payload.rstrip()[:-2], adobe=False)
            except Exception:
                pass
        if payload[:1] == b"\x78":
            try:
                payload = zlib.decompress(payload)
            except Exception:
                pass
        out += payload
    return out


def test_pdf_pill_draws_full_value_without_ellipsis():
    from reportlab.pdfgen.canvas import Canvas

    from app.radius.services.card_renderer import _pdf_pill

    buf = BytesIO()
    pdf = Canvas(buf, pagesize=(1000, 600))
    el = {
        "id": "user", "kind": "pill", "label": "USER", "value": LONG_USER,
        "x": 100, "y": 100, "width": 220, "height": 80, "padding_x": 16,
        "label_font_size": 14, "value_font_size": BIG_FONT,
        "label_color": "#64748b", "ink": "#0f172a", "surface": "#e8f7fb",
        "is_password": False, "show_label": True,
    }
    _pdf_pill(pdf, el, 600, expose_password=True)
    pdf.showPage()
    pdf.save()
    text = _pdf_decoded_text(buf.getvalue())
    assert LONG_USER.encode() in text, "القيمة يجب أن تُرسم كاملة"
    assert "…".encode() not in text
    # بعرض 220-32=188pt وخط Helvetica-Bold: 40pt لا يتسع (~289pt) —
    # وجود القيمة كاملة يثبت أن الحجم صُغِّر بدل البتر.
