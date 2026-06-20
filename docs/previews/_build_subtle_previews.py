# -*- coding: utf-8 -*-
"""Generates preview PNGs that demonstrate the *subtler* watermark-only
look (after the owner's "دفش ومبالغ فيه" feedback removed the prominent
icon). Uses PIL alpha compositing for faithful watermark rendering
because PyMuPDF/svglib drop the PDF ExtGState transparency on form-based
card output (cosmetic bug only — actual browser/PDF print is correct).

The preview uses overlay alpha 0.10 so the owner can see WHERE the motif
sits (~1.5× the production 0.04 default). Production renders even
subtler. Cards: docs/previews/themed_cards_preview.png. Hotspot:
docs/previews/hotspot_subtle_preview.png.
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
from app.radius.services import card_motifs


# Slightly higher than the 0.04 production default so the motif is
# discernible at print/PNG resolution — owner can confirm position +
# motif identity. The actual operator-rendered card uses 0.04.
PREVIEW_OVERLAY_ALPHA = 0.10


def _motif_overlay(motif: str, w: int, h: int,
                    color: str = "#000000", alpha: float = PREVIEW_OVERLAY_ALPHA) -> Image.Image:
    """Render the motif as an RGBA Pillow image with composited alpha."""
    paths = card_motifs.motif_svg(motif, 50, 50, 100,
                                    color=color, opacity=1.0)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'width="{w}" height="{h}" viewBox="0 0 100 100">{paths}</svg>')
    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(w, h))
    drawing = svg2rlg(StringIO(svg))
    renderPDF.draw(drawing, pdf, 0, 0)
    pdf.save()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=True)
    rgba = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    r, g, b, a = rgba.split()
    a = a.point(lambda v: int(v * alpha))
    return Image.merge("RGBA", (r, g, b, a))


def build_cards_preview() -> str:
    PRESETS = ["cafe_mocha", "clinic_calm", "gaming_neon"]
    MOTIFS = {"cafe_mocha": "coffee", "clinic_calm": "medical",
              "gaming_neon": "gamepad"}

    # Render WITHOUT watermark so PIL overlay can apply faithful alpha.
    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(1000, 600))
    for key in PRESETS:
        layout = _template_layout({
            "design_preset": key,
            "render_engine": "ar_horizontal",
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

    # Composite the watermark overlay on each page.
    for i, key in enumerate(PRESETS):
        page = pages[i]
        pw, ph = page.size
        wm_size = int(min(pw, ph) * 0.45)
        overlay = _motif_overlay(MOTIFS[key], wm_size, wm_size,
                                   color="#000000",
                                   alpha=PREVIEW_OVERLAY_ALPHA)
        # RTL render: watermark on the left (x=0.18 from left edge).
        wm_x = int(pw * 0.18) - wm_size // 2
        wm_y = int(ph * 0.74) - wm_size // 2
        page.alpha_composite(overlay, (wm_x, wm_y))
        pages[i] = page.convert("RGB")

    # Stack vertically with gaps.
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
    """A mock cafe hotspot page showing the subtle watermark on a mobile
    canvas. Demonstrates the watermark-only look (no corner icon)."""
    W, H = 420, 720
    page = Image.new("RGBA", (W, H), "#fff7ed")

    # Light gradient overlay (top→bottom)
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for y in range(H):
        t = y / H
        # bg_a #fff7ed → bg_b #ffedd5
        r = int(255 + (255 - 255) * t)
        g = int(247 + (237 - 247) * t)
        b = int(237 + (213 - 237) * t)
        grad.paste((r, g, b, 255), (0, y, W, y + 1))
    page = Image.alpha_composite(page, grad)

    # Watermark — coffee motif, centered, subtle.
    wm_size = int(min(W, H) * 0.55)
    overlay = _motif_overlay("coffee", wm_size, wm_size,
                               color="#000000",
                               alpha=PREVIEW_OVERLAY_ALPHA)
    wm_x = (W - wm_size) // 2
    wm_y = (H - wm_size) // 2
    page.alpha_composite(overlay, (wm_x, wm_y))

    # Mock login card overlay (white rounded rect)
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(page)
    # Title
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

    # Login card
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

    # Label
    draw.text((20, 22), "HOTSPOT (Cafe vertical) — subtle watermark only",
              fill="#475569", font=f_lbl)

    path = "docs/previews/hotspot_subtle_preview.png"
    page.convert("RGB").save(path, "PNG")
    return path


def main():
    os.makedirs("docs/previews", exist_ok=True)
    c = build_cards_preview()
    h = build_hotspot_preview()
    print("cards:", os.path.getsize(c), "bytes:", c)
    print("hotspot:", os.path.getsize(h), "bytes:", h)


if __name__ == "__main__":
    main()
