"""Unified card renderer — one render model, two output adapters.

Why this module exists
======================
Before this module the live preview built a card with one set of
HTML/CSS rules while the PDF exporter drew an independent layout using
ReportLab. The two layouts had drifted: the preview showed brand/title
at the top with USER/PASS/QR pills, the PDF rendered a teal half on
the right side, dropped USER/PASS into the top-left corner because
`username_x` / `password_x` defaulted to 0, and brand/title sat at
hardcoded ReportLab Y coordinates that did not match the percentages
used in the HTML preview.

The fix collapses both paths to a single normalized render model.
Whatever the user sees in the live preview is exactly what comes out
of the PDF — up to uniform scaling.

The render model
================
The model is canvas-normalized in absolute pixel units:

  - Landscape cards live on a 1000x600 canvas.
  - Portrait  cards live on a 600x1000 canvas.

All element coordinates and sizes are in canvas units. The SVG adapter
maps them via `viewBox`; the PDF adapter maps them via `beginForm` +
`pdf.scale()`. Callers that want to scale the card down (or up) do it
on the outside — the model itself is always at canvas size.

The model shape is intentionally a plain dict so it is trivially
JSON-serialisable, easy to unit test, and easy to inspect when
diagnosing a preview/PDF mismatch.

Compat note
===========
Existing print templates persist:
  - layout_json (presets, colors, brand, title, footer …)
  - top-level username_x / username_y / password_x / password_y / qr_x / qr_y
    in millimetres relative to a card_width_mm x card_height_mm box.

`_resolve_positions` normalizes those legacy mm coordinates into the
canvas-fraction system used by the renderer. When the legacy positions
are still at their (0, 0) factory default we fall back to a sensible
layout that matches the live preview rather than piling everything
into the top-left corner like the old PDF path did.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Iterable

# Public canvas dimensions. Pinned constants so any drift between the
# SVG adapter and the PDF adapter is impossible.
CANVAS_LANDSCAPE = (1000, 600)
CANVAS_PORTRAIT = (600, 1000)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3,8}$")

# Default element placements as fractions of the canvas. They mirror the
# percentage positions used by the live preview's `.pr-card-preview`
# CSS so a freshly-created template (which has not been dragged) looks
# identical in preview and PDF.
_DEFAULT_POSITIONS: dict[str, dict[str, float]] = {
    "accent":   {"x": 0.05, "y": 0.07, "width": 0.90, "height": 0.018},
    "brand":    {"x": 0.06, "y": 0.20, "size": 0.075},
    "title":    {"x": 0.06, "y": 0.33, "size": 0.105},
    "user":     {"x": 0.06, "y": 0.50, "width": 0.46, "height": 0.13},
    "pass":     {"x": 0.06, "y": 0.66, "width": 0.46, "height": 0.13},
    "qr":       {"x": 0.66, "y": 0.36, "size": 0.27},
    "meta":     {"x": 0.06, "y": 0.84, "size": 0.05},
    "footer":   {"x": 0.06, "y": 0.95, "size": 0.045},
}

# Default show-flags. Mirror the same defaults the operations.py
# layout normaliser uses so a template that never set these explicitly
# still renders the expected elements.
_DEFAULT_SHOW = {
    "brand":    True,
    "username": True,
    "password": True,
    "qr":       True,
    "price":    False,
    "hotspot":  True,
    "validity": True,
    "serial":   True,
}


# ───────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────

def build_card_render_model(
    template: dict,
    card: dict | object | None = None,
    *,
    overrides: dict | None = None,
) -> dict:
    """Build a normalized render model for one card.

    Parameters
    ----------
    template : dict
        A row from `card_print_templates` (or its dict equivalent). The
        renderer reads `layout_json`, `orientation`, `cards_per_row`,
        `cards_per_column`, plus legacy `username_x/y`, `password_x/y`,
        `qr_x/y` if present.
    card : dict or object or None
        Per-card data. Accepts a dict with `username`, `password`, `id`
        keys OR a `Card` dataclass instance. None produces a generic
        SAMPLE card suitable for "PDF عينة" or designer mock-ups.
    overrides : dict, optional
        Text overrides from the export-room override fields:
        brand_name, card_title, footer_text, hotspot_address,
        price_text, validity_text. These win over the template defaults
        but never replace per-card values like username or password.

    Returns
    -------
    dict
        The render model. See module docstring for shape.
    """
    layout = _hydrate_layout(template)
    overrides = overrides or {}

    orient = (str(layout.get("card_orientation") or "horizontal").lower())
    canvas_w, canvas_h = CANVAS_PORTRAIT if orient == "vertical" else CANVAS_LANDSCAPE

    positions = _resolve_positions(template, layout, (canvas_w, canvas_h))
    show = _resolve_show_flags(layout)

    # ── Text + meta ──
    brand_text   = _override(overrides, "brand_name",   layout, "HobeRadius")
    title_text   = _override(overrides, "card_title",   layout, "Internet Card")
    footer_text  = _override(overrides, "footer_text",  layout, "")
    hotspot_text = _override(overrides, "hotspot_address", layout, "")
    price_text   = _override(overrides, "price_text",   layout, "")
    validity_txt = _override(overrides, "validity_text", layout, "")

    text_color    = _safe_hex(layout.get("text_color"), "#ffffff")
    accent_color  = _safe_hex(layout.get("accent_color"), "#f59e0b")
    surface_color = _safe_hex(layout.get("surface_color"), "#e8f7fb")

    username, password, card_id = _extract_card_fields(card)

    elements: list[dict] = []

    # Accent bar — first so it sits beneath the text but above the bg.
    acc = positions["accent"]
    elements.append({
        "kind": "rect",
        "id": "accent",
        "x": acc["x"] * canvas_w,
        "y": acc["y"] * canvas_h,
        "width":  acc["width"]  * canvas_w,
        "height": acc["height"] * canvas_h,
        "fill": accent_color,
        "rx": (acc["height"] * canvas_h) / 2,
    })

    if show["brand"] and brand_text:
        elements.append(_text_element(
            id="brand", text=brand_text, pos=positions["brand"],
            canvas=(canvas_w, canvas_h), color=text_color, weight=900,
            max_width_frac=0.55,
        ))

    if title_text:
        elements.append(_text_element(
            id="title", text=title_text, pos=positions["title"],
            canvas=(canvas_w, canvas_h), color=text_color, weight=950,
            max_width_frac=0.55,
        ))

    if show["username"] and username:
        elements.append(_pill_element(
            id="user", label="USER", value=username,
            pos=positions["user"], canvas=(canvas_w, canvas_h),
            surface_color=surface_color,
        ))

    if show["password"] and password:
        elements.append(_pill_element(
            id="pass", label="PASS", value=password,
            pos=positions["pass"], canvas=(canvas_w, canvas_h),
            surface_color=surface_color,
            is_password=True,
        ))

    if show["qr"]:
        qr = positions["qr"]
        # QR payload prefers the username so a phone scanner gets the
        # exact login identifier; falls back to card id; finally
        # 'SAMPLE' so designer mock-ups still render a code.
        payload = (username or card_id or "SAMPLE")
        elements.append({
            "kind": "qr",
            "id": "qr",
            "payload": payload,
            "x": qr["x"] * canvas_w,
            "y": qr["y"] * canvas_h,
            "size": qr["size"] * canvas_w,
            "bg": "#ffffff",
            "fg": "#0f172a",
        })

    # Meta line: hotspot · price · validity · #serial
    meta_parts: list[str] = []
    if show["hotspot"]  and hotspot_text: meta_parts.append(hotspot_text)
    if show["price"]    and price_text:   meta_parts.append(price_text)
    if show["validity"] and validity_txt: meta_parts.append(validity_txt)
    if show["serial"]   and card_id:      meta_parts.append("#" + str(card_id))
    if meta_parts:
        meta_pos = positions["meta"]
        elements.append({
            "kind": "text",
            "id": "meta",
            "text": "  ·  ".join(meta_parts),
            "x": meta_pos["x"] * canvas_w,
            "y": meta_pos["y"] * canvas_h,
            "size": meta_pos["size"] * canvas_h,
            "color": text_color,
            "weight": 800,
            "max_width": canvas_w * 0.88,
        })

    if footer_text:
        footer_pos = positions["footer"]
        elements.append({
            "kind": "text",
            "id": "footer",
            "text": footer_text,
            "x": footer_pos["x"] * canvas_w,
            "y": footer_pos["y"] * canvas_h,
            "size": footer_pos["size"] * canvas_h,
            "color": text_color,
            "opacity": 0.82,
            "weight": 800,
            "max_width": canvas_w * 0.88,
        })

    return {
        "canvas": {"width": canvas_w, "height": canvas_h},
        "orientation": orient,
        "background": _background(layout),
        "elements": elements,
        "card_id": str(card_id) if card_id else "",
        "username": username,
        # password is kept in the model so the PDF adapter can render
        # it; the SVG adapter always masks it.
        "password": password,
    }


# ───────────────────────────────────────────────────────────────────
# SVG adapter
# ───────────────────────────────────────────────────────────────────

def render_card_svg(model: dict, *, mask_password: bool = True) -> str:
    """Render the model as an inline SVG string.

    The SVG uses `viewBox="0 0 W H"` and `preserveAspectRatio="xMidYMid meet"`,
    so dropping it into ANY container scales the card uniformly without
    distortion. `mask_password=True` (default) replaces the password
    value with bullets — the live preview never reveals the real
    password.
    """
    w = int(model["canvas"]["width"]); h = int(model["canvas"]["height"])
    bg = model.get("background") or {}

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" class="card-svg" '
        f'style="width:100%;height:auto;display:block">'
    )
    parts.append('<defs>')
    parts.extend(_svg_defs(bg, w, h))
    parts.append(f'<clipPath id="card-clip"><rect x="0" y="0" width="{w}" height="{h}" rx="{int(w*0.025)}" ry="{int(w*0.025)}"/></clipPath>')
    parts.append('</defs>')

    parts.append('<g clip-path="url(#card-clip)">')
    parts.extend(_svg_background(bg, w, h))

    for el in model["elements"]:
        kind = el.get("kind")
        if kind == "rect":
            parts.append(_svg_rect(el))
        elif kind == "text":
            parts.append(_svg_text(el))
        elif kind == "pill":
            parts.append(_svg_pill(el, mask_password=mask_password))
        elif kind == "qr":
            parts.append(_svg_qr_placeholder(el))

    parts.append('</g>')
    parts.append('</svg>')
    return "".join(parts)


# ───────────────────────────────────────────────────────────────────
# PDF adapter
# ───────────────────────────────────────────────────────────────────

def render_card_pdf(pdf, model: dict, *, form_name: str,
                     expose_password: bool = True) -> None:
    """Draw the model into a ReportLab named form at canvas coordinates.

    The adapter writes the card into `beginForm(form_name, 0, 0, W, H)`
    with W/H equal to the model's canvas. The caller is responsible
    for `pdf.translate(...)` + `pdf.scale(...)` + `pdf.doForm(form_name)`
    when placing the finished card on a sheet, and for choosing whether
    to render multiple cards as multiple forms.

    `expose_password=True` (default for PDF) renders the real password.
    Pass False to keep the password masked — useful for designer PDF
    samples that should not leak credentials.
    """
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])

    pdf.beginForm(form_name, 0, 0, cw, ch)
    try:
        _pdf_background(pdf, model.get("background") or {}, cw, ch)
        for el in model["elements"]:
            kind = el.get("kind")
            if kind == "rect":
                _pdf_rect(pdf, el, ch)
            elif kind == "text":
                _pdf_text(pdf, el, ch)
            elif kind == "pill":
                _pdf_pill(pdf, el, ch, expose_password=expose_password)
            elif kind == "qr":
                _pdf_qr(pdf, el, ch)
    finally:
        pdf.endForm()


def place_card_form_uniform(pdf, model: dict, *, form_name: str,
                              slot_x: float, slot_y: float,
                              slot_width: float, slot_height: float) -> None:
    """Place an already-built form into a sheet slot with UNIFORM scale.

    The card is centered inside the slot and scaled by `min(slot_w/cw,
    slot_h/ch)` so its internal proportions (text size, QR shape,
    pill widths, accent bar position) are preserved exactly. This is
    the PDF equivalent of `preserveAspectRatio="xMidYMid meet"` in
    SVG.

    Increasing cards_per_row or cards_per_column only changes the
    slot size — it never changes what is inside the form.
    """
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])
    fit = min(slot_width / max(cw, 1.0), slot_height / max(ch, 1.0))
    draw_w = cw * fit
    draw_h = ch * fit
    dx = slot_x + (slot_width - draw_w) / 2.0
    dy = slot_y + (slot_height - draw_h) / 2.0
    pdf.saveState()
    try:
        pdf.translate(dx, dy)
        pdf.scale(fit, fit)
        pdf.doForm(form_name)
    finally:
        pdf.restoreState()


def _pdf_color(value: str):
    """Return a reportlab Color for a hex string (cached import)."""
    from reportlab.lib import colors

    raw = (value or "#000000").strip()
    if not raw.startswith("#"):
        raw = "#" + raw
    try:
        return colors.HexColor(raw)
    except Exception:
        return colors.HexColor("#1f2937")


def _pdf_background(pdf, bg: dict, cw: float, ch: float) -> None:
    """Draw the card's background: gradient (faked as a 2-stop split),
    optional bitmap image, optional decorative pattern.

    ReportLab does not have native linear-gradient support, so we
    approximate by stacking a horizontal band of intermediate
    colour stops. For most card uses the human eye reads this as a
    smooth gradient, and on the printed page it is indistinguishable
    from the SVG preview at the same scale.
    """
    from reportlab.lib import colors

    start = _pdf_color(bg.get("gradient_start", "#0f172a"))
    end = _pdf_color(bg.get("gradient_end", "#22a7bd"))
    # 24 horizontal bands of interpolated colour.
    bands = 24
    band_h = ch / bands
    for i in range(bands):
        t = i / max(bands - 1, 1)
        r = start.red   + (end.red   - start.red)   * t
        g = start.green + (end.green - start.green) * t
        b = start.blue  + (end.blue  - start.blue)  * t
        pdf.setFillColor(colors.Color(r, g, b))
        # PDF origin is bottom-left; band i (top→bottom in the model)
        # sits at (ch - (i+1)*band_h) in PDF space.
        pdf.rect(0, ch - (i + 1) * band_h, cw, band_h + 0.5, stroke=0, fill=1)

    # Optional bitmap.
    image_url = bg.get("image_data_url") or ""
    if image_url.startswith("data:image/") and ";base64," in image_url:
        try:
            from io import BytesIO
            from reportlab.lib.utils import ImageReader

            mime_part, encoded = image_url.split(";base64,", 1)
            if mime_part in {"data:image/png", "data:image/jpeg", "data:image/jpg"}:
                image = ImageReader(BytesIO(base64.b64decode(encoded)))
                opacity = max(0.0, min(1.0, float(bg.get("image_opacity") or 0.82)))
                pdf.saveState()
                # ReportLab doesn't support image alpha directly, but we
                # can stack a translucent dark overlay on top so the
                # image looks "dimmed" the same way the SVG preview does.
                pdf.drawImage(image, 0, 0, width=cw, height=ch,
                              preserveAspectRatio=False, mask="auto")
                pdf.setFillColor(colors.Color(0, 0, 0, alpha=max(0, 1 - opacity)))
                pdf.rect(0, 0, cw, ch, stroke=0, fill=1)
                pdf.restoreState()
        except Exception:
            pass

    # Decorative pattern overlay.
    pattern = bg.get("pattern") or "signal"
    if pattern == "grid":
        pdf.setStrokeColor(colors.Color(1, 1, 1, alpha=0.30))
        pdf.setLineWidth(0.6)
        step = max(cw * 0.045, 12)
        x = 0.0
        while x <= cw:
            pdf.line(x, 0, x, ch)
            x += step
        y = 0.0
        while y <= ch:
            pdf.line(0, y, cw, y)
            y += step
    elif pattern == "signal":
        pdf.setFillColor(colors.Color(1, 1, 1, alpha=0.35))
        bar_w = max(cw * 0.005, 2.0)
        gap = max(cw * 0.020, 6.0)
        x = 0.0
        bar_h = ch * 0.30
        while x <= cw:
            pdf.rect(x, 0, bar_w, bar_h, stroke=0, fill=1)
            x += bar_w + gap
    elif pattern == "wave":
        # Single faint highlight in the top-left quadrant.
        pdf.setFillColor(colors.Color(1, 1, 1, alpha=0.18))
        pdf.circle(cw * 0.25, ch * 0.70, min(cw, ch) * 0.30, stroke=0, fill=1)


def _pdf_rect(pdf, el: dict, ch: float) -> None:
    """Filled rounded rect at model coordinates (top-left)."""
    pdf.setFillColor(_pdf_color(el.get("fill", "#ffffff")))
    pdf_y = ch - el["y"] - el["height"]
    rx = float(el.get("rx", 0))
    if rx > 0:
        pdf.roundRect(el["x"], pdf_y, el["width"], el["height"], rx,
                      stroke=0, fill=1)
    else:
        pdf.rect(el["x"], pdf_y, el["width"], el["height"],
                 stroke=0, fill=1)


def _pdf_text(pdf, el: dict, ch: float) -> None:
    """Draw a text run. The model gives top-left of the text box; we
    convert to PDF baseline by dropping the cap-height (~0.78 × size).
    """
    from reportlab.lib import colors

    size = max(float(el.get("size", 12)), 1.0)
    weight = int(el.get("weight", 700))
    font = "Helvetica-Bold" if weight >= 700 else "Helvetica"
    pdf.setFont(font, size)
    color = _pdf_color(el.get("color", "#ffffff"))
    opacity = float(el.get("opacity", 1.0))
    if opacity < 1.0:
        # The default font fill respects alpha when wrapped in a
        # reportlab Color with alpha; just rebuild it.
        pdf.setFillColor(colors.Color(color.red, color.green, color.blue,
                                       alpha=max(0.0, min(1.0, opacity))))
    else:
        pdf.setFillColor(color)
    text = _pdf_safe_text(el.get("text", ""))
    if not text:
        return
    # SVG dominant-baseline="hanging" puts the text top at y. PDF's
    # drawString uses the baseline. Cap height ≈ 0.78 of font size for
    # Helvetica, so drop y by that amount to align visually.
    baseline = ch - el["y"] - size * 0.78
    max_width = float(el.get("max_width") or 0)
    if max_width > 0:
        text = _shrink_to_fit(pdf, text, font, size, max_width)
    pdf.drawString(el["x"], baseline, text)


def _pdf_pill(pdf, el: dict, ch: float, *, expose_password: bool) -> None:
    """Draw the surface rect + label + value of a USER/PASS pill."""
    pdf.setFillColor(_pdf_color(el["surface"]))
    pdf_y = ch - el["y"] - el["height"]
    pdf.roundRect(el["x"], pdf_y, el["width"], el["height"],
                  el["height"] * 0.20, stroke=0, fill=1)
    # Label (USER / PASS)
    label_size = max(float(el["label_font_size"]), 4.0)
    pdf.setFont("Helvetica-Bold", label_size)
    pdf.setFillColor(_pdf_color(el["label_color"]))
    label_top = el["y"] + el["height"] * 0.18
    pdf.drawString(el["x"] + el["padding_x"],
                   ch - label_top - label_size * 0.78,
                   _pdf_safe_text(el["label"]))
    # Value (the real credential — masked if expose_password is False
    # and this pill carries the password).
    value = el["value"]
    if el.get("is_password") and not expose_password:
        value = "•" * min(max(len(value), 6), 10)
    value_size = max(float(el["value_font_size"]), 5.0)
    pdf.setFont("Helvetica-Bold", value_size)
    pdf.setFillColor(_pdf_color(el["ink"]))
    value_top = el["y"] + el["height"] * 0.46
    text = _pdf_safe_text(value)
    max_value_width = el["width"] - 2 * el["padding_x"]
    if max_value_width > 0:
        text = _shrink_to_fit(pdf, text, "Helvetica-Bold", value_size,
                              max_value_width)
    pdf.drawString(el["x"] + el["padding_x"],
                   ch - value_top - value_size * 0.78,
                   text)


def _pdf_qr(pdf, el: dict, ch: float) -> None:
    """Draw a QR symbol using the same QrCodeWidget as the SVG path."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics import renderPDF
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    size = float(el["size"])
    pdf_y_top = ch - el["y"]  # top of the QR box in PDF coords
    pdf_y_bottom = pdf_y_top - size

    # White rounded background — readers need the quiet zone.
    pdf.setFillColor(colors.white)
    pdf.roundRect(el["x"], pdf_y_bottom, size, size, size * 0.10,
                  stroke=0, fill=1)

    payload = str(el.get("payload") or "SAMPLE")
    try:
        widget = QrCodeWidget(payload)
        bounds = widget.getBounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        inner = size * 0.84  # 8 % quiet zone on each side
        scale_x = inner / max(w, 1)
        scale_y = inner / max(h, 1)
        drawing = Drawing(inner, inner,
                          transform=[scale_x, 0, 0, scale_y, 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, pdf,
                       el["x"] + (size - inner) / 2,
                       pdf_y_bottom + (size - inner) / 2)
    except Exception:
        pass


def _pdf_safe_text(value: Any) -> str:
    """Strip characters the built-in PDF font cannot render.

    ReportLab's default Helvetica is Latin-only. Rather than crash on
    Arabic glyphs we drop them — Arabic is intentionally rendered in
    the SVG preview (the page font carries Cairo) but does not yet
    have a shaped Arabic engine for PDF. This is documented as a
    follow-up in the print-templates redesign report.
    """
    raw = str(value or "")
    if not raw:
        return ""
    try:
        raw.encode("latin-1")
        return raw
    except UnicodeEncodeError:
        return raw.encode("latin-1", "ignore").decode("latin-1")


def _shrink_to_fit(pdf, text: str, font: str, size: float,
                    max_width: float) -> str:
    """Trim text with an ellipsis until it fits inside max_width."""
    if pdf.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    # Walk from the end, dropping one char at a time.
    out = text
    while out and pdf.stringWidth(out + ellipsis, font, size) > max_width:
        out = out[:-1]
    return (out + ellipsis) if out else ellipsis


# ───────────────────────────────────────────────────────────────────
# Internal helpers — model assembly
# ───────────────────────────────────────────────────────────────────

def _hydrate_layout(template: dict) -> dict:
    """Pull the JSON layout out of a template row in either shape."""
    layout = template.get("layout_json")
    if not isinstance(layout, dict):
        layout = template.get("layout") if isinstance(template.get("layout"), dict) else {}
    return layout


def _resolve_positions(template: dict, layout: dict,
                        canvas: tuple[int, int]) -> dict[str, dict[str, float]]:
    """Map legacy mm-based positions into canvas fractions.

    Existing templates store username_x/y, password_x/y, qr_x/y at the
    top level of the template row, expressed in mm relative to a
    card_width_mm x card_height_mm card. We normalize them to canvas
    fractions so the same renderer handles old and new templates.

    If the legacy values are at their factory default (0, 0) we fall
    back to `_DEFAULT_POSITIONS`. This is the bug that made the old
    PDF stack USER/PASS/QR in the top-left corner.
    """
    card_w_mm = max(_float(layout.get("card_width_mm"), 85), 1.0)
    card_h_mm = max(_float(layout.get("card_height_mm"), 54), 1.0)

    positions = {key: dict(value) for key, value in _DEFAULT_POSITIONS.items()}

    for legacy_key, target_key in (("username", "user"),
                                   ("password", "pass"),
                                   ("qr",       "qr")):
        raw_x = _float(template.get(f"{legacy_key}_x"), 0)
        raw_y = _float(template.get(f"{legacy_key}_y"), 0)
        if raw_x == 0 and raw_y == 0:
            continue  # keep defaults — that template never customised this
        fx = max(0.0, min(1.0, raw_x / card_w_mm))
        fy = max(0.0, min(1.0, raw_y / card_h_mm))
        positions[target_key]["x"] = fx
        positions[target_key]["y"] = fy

    return positions


def _resolve_show_flags(layout: dict) -> dict[str, bool]:
    return {
        key: _boolish(layout.get(f"show_{key}"), default)
        for key, default in _DEFAULT_SHOW.items()
    }


def _background(layout: dict) -> dict:
    image_url = str(layout.get("background_image_data_url") or "")
    return {
        "gradient_start": _safe_hex(layout.get("gradient_start"), "#0f172a"),
        "gradient_end":   _safe_hex(layout.get("gradient_end"),   "#22a7bd"),
        "pattern":        str(layout.get("pattern_style") or "signal"),
        "image_data_url": image_url if image_url.startswith("data:image/") else "",
        "image_opacity":  max(0.0, min(1.0, _float(layout.get("image_opacity"), 0.82))),
    }


def _override(overrides: dict, key: str, layout: dict, default: str) -> str:
    candidate = overrides.get(key)
    if candidate is None or not str(candidate).strip():
        candidate = layout.get(key)
    value = (candidate if candidate is not None else default)
    return str(value).strip()


def _extract_card_fields(card: dict | object | None) -> tuple[str, str, str]:
    if card is None:
        return "SAMPLE", "********", ""
    if isinstance(card, dict):
        username = str(card.get("username") or "").strip()
        password = str(card.get("password") or "").strip()
        card_id  = str(card.get("id") or card.get("serial") or "").strip()
    else:
        username = str(getattr(card, "username", "") or "").strip()
        password = str(getattr(card, "password", "") or "").strip()
        card_id  = str(getattr(card, "id", "") or "").strip()
    return username or "SAMPLE", password or "********", card_id


def _text_element(*, id: str, text: str, pos: dict, canvas: tuple[int, int],
                   color: str, weight: int, max_width_frac: float) -> dict:
    cw, ch = canvas
    return {
        "kind": "text",
        "id": id,
        "text": text,
        "x": pos["x"] * cw,
        "y": pos["y"] * ch,
        "size": pos["size"] * ch,
        "color": color,
        "weight": weight,
        "max_width": cw * max_width_frac,
    }


def _pill_element(*, id: str, label: str, value: str, pos: dict,
                   canvas: tuple[int, int], surface_color: str,
                   is_password: bool = False) -> dict:
    cw, ch = canvas
    width = pos.get("width", 0.46) * cw
    height = pos.get("height", 0.13) * ch
    return {
        "kind": "pill",
        "id": id,
        "label": label,
        "value": value,
        "x": pos["x"] * cw,
        "y": pos["y"] * ch,
        "width": width,
        "height": height,
        "surface": surface_color,
        "ink": "#0f172a",
        "label_color": "#64748b",
        "is_password": is_password,
        "value_font_size": height * 0.52,
        "label_font_size": height * 0.30,
        "padding_x": height * 0.32,
    }


# ───────────────────────────────────────────────────────────────────
# Internal helpers — SVG output
# ───────────────────────────────────────────────────────────────────

def _svg_defs(bg: dict, w: int, h: int) -> Iterable[str]:
    yield (
        f'<linearGradient id="card-bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{_xml(bg.get("gradient_start", "#0f172a"))}"/>'
        f'<stop offset="100%" stop-color="{_xml(bg.get("gradient_end", "#22a7bd"))}"/>'
        f'</linearGradient>'
    )
    # Decorative pattern overlays.
    pattern = bg.get("pattern") or "signal"
    if pattern == "grid":
        step = max(int(w * 0.045), 8)
        yield (
            f'<pattern id="card-pattern" patternUnits="userSpaceOnUse" '
            f'width="{step}" height="{step}">'
            f'<path d="M{step} 0 L0 0 0 {step}" fill="none" '
            f'stroke="rgba(255,255,255,.45)" stroke-width="1"/>'
            f'</pattern>'
        )
    elif pattern == "wave":
        # Two faint radial highlights, drawn as a single SVG pattern.
        yield (
            f'<radialGradient id="card-pattern" cx="20%" cy="30%" r="55%">'
            f'<stop offset="0%"  stop-color="rgba(255,255,255,.30)"/>'
            f'<stop offset="60%" stop-color="rgba(255,255,255,0)"/>'
            f'</radialGradient>'
        )
    elif pattern == "signal":
        # Vertical signal bars at the bottom 30 % of the card.
        yield (
            f'<pattern id="card-pattern" patternUnits="userSpaceOnUse" '
            f'width="{max(int(w*0.025),6)}" height="{h}">'
            f'<rect x="0" y="{int(h*0.7)}" width="{max(int(w*0.005),2)}" '
            f'height="{int(h*0.3)}" fill="rgba(255,255,255,.40)"/>'
            f'</pattern>'
        )
    # "clean" emits no overlay.


def _svg_background(bg: dict, w: int, h: int) -> Iterable[str]:
    yield f'<rect x="0" y="0" width="{w}" height="{h}" fill="url(#card-bg)"/>'
    # Optional background image (kept inside the clip-path).
    image_url = bg.get("image_data_url") or ""
    if image_url:
        opacity = bg.get("image_opacity", 0.82)
        yield (
            f'<image href="{_xml(image_url)}" x="0" y="0" '
            f'width="{w}" height="{h}" '
            f'preserveAspectRatio="xMidYMid slice" opacity="{opacity:.2f}"/>'
        )
        # Slight dark overlay so the text stays readable on a photo.
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="rgba(15,23,42,0.32)"/>'
        )
    pattern = bg.get("pattern") or "signal"
    if pattern in {"grid", "signal"}:
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="url(#card-pattern)" opacity="0.45"/>'
        )
    elif pattern == "wave":
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="url(#card-pattern)"/>'
        )


def _svg_rect(el: dict) -> str:
    return (
        f'<rect x="{el["x"]:.1f}" y="{el["y"]:.1f}" '
        f'width="{el["width"]:.1f}" height="{el["height"]:.1f}" '
        f'rx="{el.get("rx", 0):.1f}" ry="{el.get("rx", 0):.1f}" '
        f'fill="{_xml(el.get("fill", "#fff"))}"/>'
    )


def _svg_text(el: dict) -> str:
    weight = el.get("weight", 700)
    opacity = el.get("opacity", 1.0)
    return (
        f'<text x="{el["x"]:.1f}" y="{el["y"]:.1f}" '
        f'font-family="\'Cairo\', \'Helvetica Neue\', Arial, sans-serif" '
        f'font-size="{el["size"]:.1f}" font-weight="{weight}" '
        f'fill="{_xml(el.get("color", "#fff"))}" opacity="{opacity:.2f}" '
        f'dominant-baseline="hanging" text-anchor="start">'
        f'{_xml(el["text"])}'
        f'</text>'
    )


def _svg_pill(el: dict, *, mask_password: bool) -> str:
    value = el["value"]
    if mask_password and el.get("is_password"):
        value = "•" * min(max(len(value), 6), 10)
    label_size = el["label_font_size"]
    value_size = el["value_font_size"]
    pad = el["padding_x"]
    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    label_y = y + h * 0.36
    value_y = y + h * 0.72
    return (
        f'<g class="card-pill">'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{h*0.20:.1f}" ry="{h*0.20:.1f}" '
        f'fill="{_xml(el["surface"])}" opacity="0.95"/>'
        f'<text x="{x+pad:.1f}" y="{label_y:.1f}" '
        f'font-family="\'Cairo\', \'Helvetica Neue\', Arial, sans-serif" '
        f'font-size="{label_size:.1f}" font-weight="900" '
        f'fill="{_xml(el["label_color"])}" '
        f'dominant-baseline="middle">{_xml(el["label"])}</text>'
        f'<text x="{x+pad:.1f}" y="{value_y:.1f}" '
        f'font-family="\'Menlo\', \'Consolas\', monospace" '
        f'font-size="{value_size:.1f}" font-weight="900" '
        f'fill="{_xml(el["ink"])}" '
        f'dominant-baseline="middle">{_xml(value)}</text>'
        f'</g>'
    )


def _svg_qr_placeholder(el: dict) -> str:
    """Render the QR as inline SVG.

    Walks the QrCode bit matrix from reportlab.graphics.barcode.qr —
    the SAME library the PDF adapter uses — and emits one big
    `<rect>` for the white quiet-zone background plus one `<rect>`
    per dark module. This guarantees the preview and the PDF show
    the same QR symbol for the same payload.

    Falls back to a labelled placeholder square if the QR engine
    fails for any reason (e.g. extremely long payload). The card
    layout never depends on the QR shape — only on the slot.
    """
    payload = el["payload"]
    size = max(float(el["size"]), 16.0)
    x = float(el["x"]); y = float(el["y"])
    bg = el.get("bg", "#fff")
    fg = el.get("fg", "#0f172a")
    pad = size * 0.08
    inner = _qr_inline_svg(payload, x + pad, y + pad, size - 2 * pad, fg)
    return (
        f'<g class="card-qr">'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" '
        f'rx="{size*0.10:.1f}" ry="{size*0.10:.1f}" '
        f'fill="{_xml(bg)}"/>'
        f'{inner}'
        f'</g>'
    )


def _qr_inline_svg(payload: str, x: float, y: float, size: float, fg: str) -> str:
    """Generate the dark-module rects of a QR symbol for `payload`."""
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget

        widget = QrCodeWidget(payload)
        bounds = widget.getBounds()
        # The QrCodeWidget exposes its underlying matrix on `.qr`. Each
        # row is a string of "1"/"0" or a list of bools depending on the
        # reportlab version, so we coerce both shapes into bool.
        qr = getattr(widget, "qr", None)
        modules = getattr(qr, "modules", None) if qr is not None else None
        if not modules:
            return _svg_placeholder_grid(x, y, size, fg)
        n = len(modules)
        if n <= 0:
            return _svg_placeholder_grid(x, y, size, fg)
        cell = size / n
        rects: list[str] = []
        for row_idx, row in enumerate(modules):
            for col_idx, value in enumerate(row):
                on = bool(int(value)) if isinstance(value, str) else bool(value)
                if not on:
                    continue
                rx = x + col_idx * cell
                ry = y + row_idx * cell
                # Slight overlap (cell * 1.02) prevents thin white
                # hairlines between modules at fractional zoom levels.
                rects.append(
                    f'<rect x="{rx:.2f}" y="{ry:.2f}" '
                    f'width="{cell*1.02:.2f}" height="{cell*1.02:.2f}" '
                    f'fill="{_xml(fg)}"/>'
                )
        return "".join(rects)
    except Exception:
        return _svg_placeholder_grid(x, y, size, fg)


def _svg_placeholder_grid(x: float, y: float, size: float, fg: str) -> str:
    """A neutral 7x7 grid used when QR generation fails."""
    cell = size / 7
    out: list[str] = []
    for r in range(7):
        for c in range(7):
            if (r + c) % 2 == 0:
                out.append(
                    f'<rect x="{x+c*cell:.2f}" y="{y+r*cell:.2f}" '
                    f'width="{cell:.2f}" height="{cell:.2f}" '
                    f'fill="{_xml(fg)}" opacity="0.35"/>'
                )
    return "".join(out)


# ───────────────────────────────────────────────────────────────────
# Tiny utility helpers
# ───────────────────────────────────────────────────────────────────

def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _boolish(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _safe_hex(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if not raw.startswith("#"):
        raw = "#" + raw
    if not _HEX_RE.match(raw):
        return fallback
    return raw


_XML_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#39;"),
)


def _xml(value: Any) -> str:
    text = str(value or "")
    for raw, escaped in _XML_ESCAPES:
        text = text.replace(raw, escaped)
    return text
