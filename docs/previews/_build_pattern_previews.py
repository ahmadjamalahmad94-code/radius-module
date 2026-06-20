# -*- coding: utf-8 -*-
"""Builds preview PNGs of the SEAMLESS TILED PATTERN look.

Replaces the prior single-shape watermark previews. Composites the actual
SVG <pattern> tile (rasterized once) into a tiled overlay so the owner
sees what the real browser/print render looks like (PyMuPDF + ReportLab
both have rough alpha handling on form-based PDFs; PIL gives faithful
output).

Outputs:
  docs/previews/themed_cards_preview.png   (cafe Mocha + clinic Calm)
  docs/previews/hotspot_pattern_cafe.png   (cafe hotspot mock)
"""
from __future__ import annotations

import os
from io import BytesIO, StringIO

from PIL import Image
import pymupdf
from reportlab.pdfgen import canvas as _canvas
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

from app.radius.services.operations import _template_layout
from app.radius.services.card_renderer import build_card_render_model, render_card_pdf
from app.radius.services import card_motif_patterns as cmp


# Slightly higher than the 0.06 production default so the motif network
# is clearly readable at PNG resolution. Production renders at 6%.
PREVIEW_PATTERN_ALPHA = 0.16
TILE_PX = 220


def _tile_image(vertical: str, color: str = "#0f172a") -> Image.Image:
    """Render the per-vertical pattern tile as an RGBA Pillow image with
    composited alpha so the tile can be repeated cleanly via Image.paste."""
    paths = cmp.build_tile_paths(vertical, tile_size=float(TILE_PX),
                                   motif_size=48.0, stroke_width=1.4)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{TILE_PX}" height="{TILE_PX}" '
        f'viewBox="0 0 {TILE_PX} {TILE_PX}" '
        f'color="{color}" fill="none" stroke="{color}">'
        f'{paths.replace("currentColor", color)}</svg>'
    )
    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(TILE_PX, TILE_PX))
    drawing = svg2rlg(StringIO(svg))
    renderPDF.draw(drawing, pdf, 0, 0)
    pdf.save()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=True)
    rgba = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    r, g, b, a = rgba.split()
    a = a.point(lambda v: int(v * PREVIEW_PATTERN_ALPHA))
    return Image.merge("RGBA", (r, g, b, a))


def _tile_fill(canvas: Image.Image, tile: Image.Image,
                inset: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Image.Image:
    """Tile the given motif image across the canvas, clipped to inset."""
    cw, ch = canvas.size
    tw, th = tile.size
    for y in range(inset[1], ch - inset[3], th):
        for x in range(inset[0], cw - inset[2], tw):
            canvas.alpha_composite(tile, (x, y))
    return canvas


def build_cards_preview() -> str:
    PRESETS = ["cafe_mocha", "clinic_calm"]
    MOTIF_MAP = {"cafe_mocha": "cafe", "clinic_calm": "clinic"}

    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(1000, 600))
    for key in PRESETS:
        layout = _template_layout({
            "design_preset": key,
            "render_engine": "ar_horizontal",
            # render without the pattern so the PIL overlay can apply
            # faithful alpha.
            "watermark_enabled": "0",
        })
        template = {"id": 1, "name": "t", "orientation": "landscape",
                     "layout_json": layout}
        model = build_card_render_model(template,
            {"id": "915", "username": "card-915", "password": "Pw_9152"})
        render_card_pdf(pdf, model, form_name=f"card_{key}",
                         expose_password=True, include_background=True)
        pdf.doForm(f"card_{key}")
        pdf.showPage()
    pdf.save()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    pages = []
    for page in doc:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        pages.append(Image.frombytes("RGB", (pix.width, pix.height),
                                        pix.samples).convert("RGBA"))

    # Tile the per-vertical motif pattern across each card.
    for i, key in enumerate(PRESETS):
        page = pages[i]
        vertical = MOTIF_MAP[key]
        # Use a dark monochrome tint so the pattern reads on the colored
        # gradient background.
        tile = _tile_image(vertical, color="#0f172a")
        page = _tile_fill(page, tile)
        pages[i] = page.convert("RGB")

    # Stack vertically.
    W = max(p.width for p in pages)
    H = sum(p.height for p in pages) + 30 * (len(pages) + 1)
    out = Image.new("RGB", (W, H), "#f8fafc")
    y = 30
    for p in pages:
        out.paste(p, (0, y))
        y += p.height + 30
    path = "docs/previews/themed_cards_preview.png"
    out.save(path, "PNG")
    return path


def build_hotspot_preview() -> str:
    """Cafe hotspot mock with the seamless coffee pattern tiled across
    the entire background."""
    W, H = 420, 720
    # Cream cafe-style background gradient
    page = Image.new("RGBA", (W, H), "#fff7ed")
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for y in range(H):
        t = y / H
        g = int(247 + (237 - 247) * t)
        b = int(237 + (213 - 237) * t)
        grad.paste((255, g, b, 255), (0, y, W, y + 1))
    page = Image.alpha_composite(page, grad)

    # Tile the cafe pattern (dark accent color, low alpha).
    tile = _tile_image("cafe", color="#3a1a08")
    page = _tile_fill(page, tile)

    # Mock login card overlay (opaque white card on top so legibility is
    # preserved — same as production).
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(page)
    try:
        f_brand = ImageFont.truetype("arial.ttf", 28)
        f_tag = ImageFont.truetype("arial.ttf", 14)
        f_lbl = ImageFont.truetype("arial.ttf", 11)
        f_inp = ImageFont.truetype("arial.ttf", 14)
        f_btn = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        f_brand = f_tag = f_lbl = f_inp = f_btn = ImageFont.load_default()
    draw.text((W // 2, 140), "Mithaq Cafe", fill="#0f172a", font=f_brand,
              anchor="mm")
    draw.text((W // 2, 175), "Welcome to our network", fill="#475569",
              font=f_tag, anchor="mm")

    # White rounded login card (opaque — pattern is behind it).
    draw.rounded_rectangle((40, 240, W - 40, 480), radius=14,
                            fill="#ffffff", outline="#e2e8f0", width=1)
    draw.text((60, 260), "Username", fill="#64748b", font=f_lbl)
    draw.rounded_rectangle((60, 285, W - 60, 320), radius=8, fill="#f1f5f9")
    draw.text((74, 297), "card-915", fill="#94a3b8", font=f_inp)
    draw.text((60, 335), "Password", fill="#64748b", font=f_lbl)
    draw.rounded_rectangle((60, 360, W - 60, 395), radius=8, fill="#f1f5f9")
    draw.text((74, 372), "•" * 9, fill="#94a3b8", font=f_inp)
    draw.rounded_rectangle((60, 415, W - 60, 460), radius=10,
                            fill="#7c3a1d")
    draw.text((W // 2, 437), "Login", fill="#ffffff", font=f_btn, anchor="mm")
    draw.text((W // 2, 500), "WiFi by Hoberadius", fill="#64748b",
              font=f_lbl, anchor="mm")

    # Top label
    draw.text((20, 22), "HOTSPOT (Cafe) - seamless pattern background",
              fill="#475569", font=f_lbl)

    path = "docs/previews/hotspot_pattern_cafe.png"
    page.convert("RGB").save(path, "PNG")
    return path


def main():
    os.makedirs("docs/previews", exist_ok=True)
    c = build_cards_preview()
    h = build_hotspot_preview()
    print("cards:", os.path.getsize(c), "bytes")
    print("hotspot:", os.path.getsize(h), "bytes")


if __name__ == "__main__":
    main()
