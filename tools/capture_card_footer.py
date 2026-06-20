# -*- coding: utf-8 -*-
"""Preview PNG proving the card FOOTER/tagline is lifted off the bottom edge
with safe clearance (fix/card-footer-clip).

Renders real cards via the production renderer (render_card_pdf → PyMuPDF
raster), tiles the seamless motif pattern via PIL (faithful alpha, same as
docs/previews/_build_pattern_previews.py), then draws a faint dashed guide at
the bottom SAFE-AREA line so the footer clearance is obvious to the eye.

Cards: cafe (landscape) + clinic (landscape) + cafe (vertical) — both
orientations, Arabic tagline, serial #128.

Output: preview/card_footer_preview.png
Run:  python tools/capture_card_footer.py
"""
from __future__ import annotations

import os
import sys
from io import BytesIO, StringIO

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from PIL import Image, ImageDraw
import pymupdf
from reportlab.pdfgen import canvas as _canvas
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

from app.radius.services.operations import _template_layout
from app.radius.services.card_renderer import (
    build_card_render_model, render_card_pdf, _CARD_SAFE_BOTTOM,
)
from app.radius.services import card_motif_patterns as cmp

PATTERN_ALPHA = 0.30
TILE_PX = 220
TAGLINE = "استمتع بقهوتك على إنترنت سريع"

# (preset, motif, engine, card_w_mm, card_h_mm)
CARDS = [
    ("cafe_mocha",  "cafe",   "ar_horizontal", 85, 54),
    ("clinic_calm", "clinic", "ar_horizontal", 85, 54),
    ("cafe_mocha",  "cafe",   "ar_vertical",   54, 85),
]


def _tile_image(vertical: str, color: str) -> Image.Image:
    paths = cmp.build_tile_paths(vertical, tile_size=float(TILE_PX),
                                 motif_size=48.0, stroke_width=1.4)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{TILE_PX}" '
           f'height="{TILE_PX}" viewBox="0 0 {TILE_PX} {TILE_PX}" '
           f'color="{color}" fill="none" stroke="{color}">'
           f'{paths.replace("currentColor", color)}</svg>')
    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(TILE_PX, TILE_PX))
    renderPDF.draw(svg2rlg(StringIO(svg)), pdf, 0, 0)
    pdf.save()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=True)
    rgba = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    r, g, b, a = rgba.split()
    a = a.point(lambda v: int(v * PATTERN_ALPHA))
    return Image.merge("RGBA", (r, g, b, a))


def _tile_fill(canvas: Image.Image, tile: Image.Image) -> Image.Image:
    cw, ch = canvas.size
    tw, th = tile.size
    for y in range(0, ch, th):
        for x in range(0, cw, tw):
            canvas.alpha_composite(tile, (x, y))
    return canvas


def _dashed_hline(draw: ImageDraw.ImageDraw, x0: int, x1: int, y: int,
                  color: tuple, dash: int = 14, gap: int = 10, width: int = 2):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=width)
        x += dash + gap


def _render_card(preset: str, motif: str, engine: str,
                 card_w_mm: int, card_h_mm: int) -> Image.Image:
    layout = _template_layout({
        "design_preset": preset, "render_engine": engine,
        "watermark_enabled": "0",  # pattern applied via faithful PIL overlay
        "footer_text": TAGLINE,
        "card_width_mm": card_w_mm, "card_height_mm": card_h_mm,
        "card_orientation": "vertical" if card_h_mm > card_w_mm else "horizontal",
    })
    tint = layout.get("text_color") or "#0f172a"
    template = {"id": 1, "name": "t",
                "orientation": "portrait" if card_h_mm > card_w_mm else "landscape",
                "layout_json": layout}
    model = build_card_render_model(
        template, {"id": "128", "username": "7772", "password": "Pw_9152"})
    # Size the PDF page to the model's own canvas so vertical/landscape align.
    w, h = model["canvas"]["width"], model["canvas"]["height"]
    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(w, h))
    render_card_pdf(pdf, model, form_name="card", expose_password=True,
                    include_background=True)
    pdf.doForm("card")
    pdf.showPage()
    pdf.save()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
    card = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    card = _tile_fill(card, _tile_image(motif, tint))
    # Dashed safe-area guide near the bottom so footer clearance is visible.
    cw, chh = card.size
    draw = ImageDraw.Draw(card)
    _dashed_hline(draw, 8, cw - 8, int(chh * _CARD_SAFE_BOTTOM), (244, 63, 94, 255))
    return card.convert("RGB")


def main() -> None:
    cards = [_render_card(*c[:5]) for c in CARDS]
    pad = 28
    W = max(c.width for c in cards) + pad * 2
    H = sum(c.height for c in cards) + pad * (len(cards) + 1)
    out = Image.new("RGB", (W, H), "#0b1220")
    y = pad
    for c in cards:
        out.paste(c, ((W - c.width) // 2, y))
        y += c.height + pad
    os.makedirs(os.path.join(REPO, "preview"), exist_ok=True)
    path = os.path.join(REPO, "preview", "card_footer_preview.png")
    out.save(path, "PNG")
    print("PREVIEW:", path)


if __name__ == "__main__":
    main()
