# -*- coding: utf-8 -*-
"""Builds standalone preview PNGs of cafe + clinic hotspot pages showing
the themed motif touch. Uses the same `card_motifs.motif_symbol_paths`
output that gets injected into the live hotspot pages, plus a faithful
mockup of the login card. Rasterizes via ReportLab PDF → PyMuPDF PNG."""
from __future__ import annotations

import os

from io import BytesIO

from reportlab.pdfgen import canvas as _canvas
from reportlab.lib.colors import HexColor

from app.radius.services.card_motifs import motif_symbol_paths

# ──────────────────────────────────────────────────────────────────
# Use ReportLab to draw a mock "mobile hotspot login page" (420x720)
# with: gradient header, brand + tagline, big motif watermark
# behind, small motif icon top-corner, login form box mockup.
# ──────────────────────────────────────────────────────────────────

W, H = 420, 720


def _hexa(hex_str: str, alpha: float = 1.0):
    c = HexColor(hex_str)
    return (c.red, c.green, c.blue, alpha)


def _draw_motif_paths(pdf, motif: str, cx: float, cy: float, size: float,
                       color: str, opacity: float) -> None:
    """Render the same motif used in the live hotspot injection via
    svglib so the preview is byte-faithful to what the user sees."""
    from io import StringIO
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPDF

    paths = motif_symbol_paths(motif)  # uses currentColor
    # Wrap in an SVG with the requested color via the parent style.
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size:.0f}" height="{size:.0f}" '
        f'viewBox="0 0 100 100" '
        f'color="{color}" style="color:{color};opacity:{opacity}" '
        f'fill="{color}" stroke="{color}">'
        f'{paths.replace("currentColor", color)}'
        f'</svg>'
    )
    drawing = svg2rlg(StringIO(svg))
    if drawing is None:
        return
    # ReportLab: y is bottom-up; place so motif centers at (cx, cy).
    pdf.saveState()
    pdf.setFillAlpha(opacity)
    pdf.setStrokeAlpha(opacity)
    renderPDF.draw(drawing, pdf, cx - size / 2, H - cy - size / 2)
    pdf.restoreState()


def _draw_page(pdf, *, brand: str, motif: str, accent: str, bg_a: str,
               bg_b: str, welcome: str, label: str) -> None:
    # Background gradient (top-to-bottom).
    a = HexColor(bg_a)
    b = HexColor(bg_b)
    for i in range(60):
        t = i / 59
        r = a.red + (b.red - a.red) * t
        g = a.green + (b.green - a.green) * t
        bl = a.blue + (b.blue - a.blue) * t
        pdf.setFillColorRGB(r, g, bl)
        pdf.rect(0, H - (i + 1) * (H / 60), W, H / 60 + 1, stroke=0, fill=1)

    # Big watermark motif (behind everything), 10% opacity.
    _draw_motif_paths(pdf, motif, cx=W / 2, cy=H / 2 + 60, size=300,
                       color="#0f172a", opacity=0.10)

    # Top-corner brand icon, 88% opacity, accent color.
    _draw_motif_paths(pdf, motif, cx=W - 36, cy=36, size=44,
                       color=accent, opacity=0.92)

    # Label at very top showing the verticality.
    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColor(HexColor("#475569"))
    pdf.drawString(20, H - 22, label)

    # Brand block.
    pdf.setFont("Helvetica-Bold", 22)
    pdf.setFillColor(HexColor("#0f172a"))
    pdf.drawCentredString(W / 2, H - 140, brand)
    pdf.setFont("Helvetica", 11)
    pdf.setFillColor(HexColor("#475569"))
    pdf.drawCentredString(W / 2, H - 162, welcome)

    # Login card mockup (white card with rounded corners).
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.setStrokeColor(HexColor("#e2e8f0"))
    pdf.setLineWidth(1)
    pdf.roundRect(40, H - 480, W - 80, 240, 14, stroke=1, fill=1)

    # Username field
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(HexColor("#64748b"))
    pdf.drawString(60, H - 270, "اسم المستخدم")
    pdf.setFillColor(HexColor("#f1f5f9"))
    pdf.roundRect(60, H - 310, W - 120, 32, 8, stroke=0, fill=1)
    pdf.setFont("Helvetica", 12)
    pdf.setFillColor(HexColor("#94a3b8"))
    pdf.drawString(74, H - 297, "card-915")

    # Password field
    pdf.setFillColor(HexColor("#64748b"))
    pdf.setFont("Helvetica", 9)
    pdf.drawString(60, H - 330, "كلمة المرور")
    pdf.setFillColor(HexColor("#f1f5f9"))
    pdf.roundRect(60, H - 370, W - 120, 32, 8, stroke=0, fill=1)
    pdf.setFont("Helvetica", 12)
    pdf.setFillColor(HexColor("#94a3b8"))
    pdf.drawString(74, H - 357, "•••••••••")

    # Login button (accent color)
    pdf.setFillColor(HexColor(accent))
    pdf.roundRect(60, H - 420, W - 120, 38, 10, stroke=0, fill=1)
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(W / 2, H - 407, "تسجيل الدخول")

    # Footer note.
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(HexColor("#64748b"))
    pdf.drawCentredString(W / 2, H - 460, "WiFi by Hoberadius")


def build_one(vertical: str, motif: str, brand_ar_unused: str,
              welcome_ar_unused: str, accent: str, bg_a: str, bg_b: str,
              label: str) -> str:
    """Build a single hotspot preview as PDF then convert to PNG."""
    out_pdf = f"docs/previews/hotspot_{vertical}.pdf"
    out_png = f"docs/previews/hotspot_{vertical}.png"
    buf = BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=(W, H))
    # NB: We pass English labels because ReportLab's default font does
    # not include Arabic glyphs; the actual hotspot page uses Almarai
    # via inject_almarai_fontface and renders Arabic correctly. This
    # preview shows the visual structure + motif themed touch.
    _draw_page(pdf, brand=label.split(" - ")[1] if " - " in label else label,
                motif=motif, accent=accent, bg_a=bg_a, bg_b=bg_b,
                welcome="Welcome to our network", label=label.split(" - ")[0])
    pdf.showPage()
    pdf.save()
    with open(out_pdf, "wb") as f:
        f.write(buf.getvalue())

    import pymupdf
    from PIL import Image
    doc = pymupdf.open(out_pdf)
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img.save(out_png, "PNG")
    return out_png


def main():
    os.makedirs("docs/previews", exist_ok=True)
    out1 = build_one(
        vertical="cafe", motif="coffee",
        brand_ar_unused="",
        welcome_ar_unused="",
        accent="#7c3a1d", bg_a="#fff7ed", bg_b="#ffedd5",
        label="CAFE - Mithaq Cafe",
    )
    out2 = build_one(
        vertical="clinic", motif="medical",
        brand_ar_unused="",
        welcome_ar_unused="",
        accent="#0c4a6e", bg_a="#f0f9ff", bg_b="#e0f2fe",
        label="CLINIC - Jasmine Clinic",
    )
    print("cafe:", os.path.getsize(out1), "bytes")
    print("clinic:", os.path.getsize(out2), "bytes")


if __name__ == "__main__":
    main()
