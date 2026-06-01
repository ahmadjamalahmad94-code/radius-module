"""Unified card renderer — one render model, two output adapters.

Arabic support note
===================
Card text (brand, title, footer, hotspot, …) can be any mix of Arabic
and Latin. ReportLab's built-in Helvetica covers Latin only, so we
ship the Almarai TTF font under app/static/fonts/ and register it on
first import. The PDF adapter inspects each text run:

  - All-Latin    → Helvetica / Helvetica-Bold (unchanged)
  - Contains AR → Almarai / Almarai-Bold, after the run is reshaped
                   with `arabic-reshaper` (joins isolated letters into
                   their initial / medial / final / isolated glyph
                   forms) and re-ordered with `python-bidi` (so the
                   text flows right-to-left visually even when the PDF
                   only knows about LTR glyph runs).

The SVG adapter keeps the root geometry LTR so positions remain stable,
then sets direction per text element. Arabic headings/footers can render
RTL while credentials remain LTR so card numbers and passwords never get
reordered.


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
from io import BytesIO
import math
import os
import re
import uuid
from typing import Any, Iterable
from urllib.parse import urlencode

# Public canvas dimensions. Pinned constants so any drift between the
# SVG adapter and the PDF adapter is impossible.
CANVAS_LANDSCAPE = (1000, 600)
CANVAS_PORTRAIT = (600, 1000)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3,8}$")

# ─── Arabic font + shaping ─────────────────────────────────────────
# Almarai is shipped under app/static/fonts/. Registration is lazy so
# importing this module never fails — if ReportLab or the TTF is
# missing for any reason the PDF adapter quietly falls back to
# Helvetica and the text-strip behaviour, exactly like before.

_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "static", "fonts",
)
_ALMARAI_REGULAR_PATH = os.path.join(_FONTS_DIR, "Almarai-Regular.ttf")
_ALMARAI_BOLD_PATH = os.path.join(_FONTS_DIR, "Almarai-Bold.ttf")

PDF_FONT_LATIN = "Helvetica"
PDF_FONT_LATIN_BOLD = "Helvetica-Bold"
PDF_FONT_ARABIC = "Almarai"
PDF_FONT_ARABIC_BOLD = "Almarai-Bold"

# Arabic block ranges that should trigger the Almarai path.
#   U+0600–U+06FF  Arabic
#   U+0750–U+077F  Arabic Supplement
#   U+08A0–U+08FF  Arabic Extended-A
#   U+FB50–U+FDFF  Arabic Presentation Forms-A
#   U+FE70–U+FEFF  Arabic Presentation Forms-B
_ARABIC_RE = re.compile(
    r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]"
)

_arabic_fonts_ready: bool | None = None
_arabic_text_image_cache: dict[tuple[Any, ...], tuple[bytes, int, int]] = {}
_uploaded_background_reader_cache: dict[str, Any] = {}


def _ensure_arabic_fonts() -> bool:
    """Register Almarai with ReportLab. Cached after the first call."""
    global _arabic_fonts_ready
    if _arabic_fonts_ready is not None:
        return _arabic_fonts_ready
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        if not (os.path.isfile(_ALMARAI_REGULAR_PATH)
                and os.path.isfile(_ALMARAI_BOLD_PATH)):
            _arabic_fonts_ready = False
            return False
        # Re-registering the same font is a no-op in ReportLab, but we
        # only do it once anyway.
        pdfmetrics.registerFont(TTFont(PDF_FONT_ARABIC, _ALMARAI_REGULAR_PATH))
        pdfmetrics.registerFont(TTFont(PDF_FONT_ARABIC_BOLD, _ALMARAI_BOLD_PATH))
        _arabic_fonts_ready = True
    except Exception:  # pragma: no cover — defensive
        _arabic_fonts_ready = False
    return _arabic_fonts_ready


def _has_arabic(text: str) -> bool:
    return bool(text) and bool(_ARABIC_RE.search(text))


def _text_direction(text: str, configured: str | None = None) -> str:
    """Return `rtl` or `ltr` for SVG/PDF text alignment.

    Template authors can force all card copy RTL/LTR, but the default is
    `auto`: Arabic strings align RTL while English, numbers, usernames,
    passwords, and QR payloads stay LTR.
    """
    direction = str(configured or "auto").strip().lower()
    if direction in {"rtl", "ltr"}:
        return direction
    return "rtl" if _has_arabic(text) else "ltr"


def _render_direction(
    configured: str | None,
    *,
    card_copy: str,
    credential_label_language: str,
) -> str:
    """Pick one of the four render engines: rtl/ltr x orientation.

    Orientation is handled by canvas size; this helper chooses the
    language half. RTL is not just text direction: the card composition
    is mirrored so Arabic text sits on the right and QR/barcode moves to
    the left, avoiding overlap.
    """
    direction = str(configured or "auto").strip().lower()
    if direction in {"rtl", "ltr"}:
        return direction
    if str(credential_label_language or "").lower() == "arabic":
        return "rtl"
    return "rtl" if _has_arabic(card_copy) else "ltr"


def _shape_arabic(text: str) -> str:
    """Apply arabic-reshaper + bidi so ReportLab can lay out RTL text.

    arabic-reshaper turns isolated Unicode letters into their proper
    initial/medial/final/isolated presentation forms. python-bidi then
    applies the Unicode Bidirectional Algorithm so the resulting
    glyphs end up in visual (RTL) order — which is what ReportLab
    will draw left-to-right but the human reads right-to-left.

    Falls back to the original text if either library fails, so a
    missing dependency at runtime never blows up the PDF export.
    """
    if not text:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:  # pragma: no cover — defensive
        return text


def _pick_pdf_font(text: str, *, bold: bool) -> str:
    """Choose the right font for a text run.

    Arabic strings need Almarai (shipped TTF). Pure Latin strings stay
    on Helvetica so existing receipts look identical to before. If
    Almarai isn't available for any reason we fall back to Helvetica
    — the Arabic glyphs won't render, but the PDF still opens.
    """
    if _has_arabic(text) and _ensure_arabic_fonts():
        return PDF_FONT_ARABIC_BOLD if bold else PDF_FONT_ARABIC
    return PDF_FONT_LATIN_BOLD if bold else PDF_FONT_LATIN


def _rgba_from_pdf_color(value: str, *, opacity: float = 1.0) -> tuple[int, int, int, int]:
    color = _pdf_color(value)
    alpha = max(0.0, min(1.0, opacity))
    return (
        int(max(0, min(255, round(color.red * 255)))),
        int(max(0, min(255, round(color.green * 255)))),
        int(max(0, min(255, round(color.blue * 255)))),
        int(round(alpha * 255)),
    )


def _font_path_for_arabic(*, bold: bool) -> str:
    """Pick a font that can draw Arabic presentation-form glyphs.

    The raster path receives text after arabic-reshaper converts it to
    Unicode presentation forms. The bundled Almarai font is fine for
    normal Arabic shaping in browsers, but Pillow without libraqm draws
    some Almarai presentation forms as thin placeholder bars. Prefer
    system Arabic fonts known to contain those glyphs, then fall back to
    bundled Almarai so exports still work on minimal installs.
    """
    candidates = [
        # Windows dev/customer machines.
        r"C:\Windows\Fonts\tahomabd.ttf" if bold else r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arabtype.ttf",
        r"C:\Windows\Fonts\trado.ttf",
        # Common Linux VPS font packages.
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return _ALMARAI_BOLD_PATH if bold and os.path.isfile(_ALMARAI_BOLD_PATH) else _ALMARAI_REGULAR_PATH


def _fit_arabic_raw_text(raw_text: str, *, font, max_width: int) -> str:
    """Return raw Arabic text that fits after shaping.

    We trim before shaping because the PDF raster path draws the shaped
    visual string into a fixed canvas. This mirrors `_shrink_to_fit`
    for vector text but avoids cutting glyphs mid-image.
    """
    if max_width <= 0:
        return raw_text
    text = raw_text
    ellipsis = "…"
    while text:
        shaped = _shape_arabic(text)
        bbox = font.getbbox(shaped)
        if (bbox[2] - bbox[0]) <= max_width:
            return text
        text = text[:-1]
    shaped_ellipsis = _shape_arabic(ellipsis)
    bbox = font.getbbox(shaped_ellipsis)
    return ellipsis if (bbox[2] - bbox[0]) <= max_width else ""


def _build_arabic_text_image(
    raw_text: str,
    *,
    size: float,
    color: str,
    weight: int = 700,
    max_width: float = 0,
    direction: str = "rtl",
    opacity: float = 1.0,
) -> tuple[bytes, int, int] | None:
    """Rasterize an Arabic text run to a transparent PNG.

    ReportLab can embed the Almarai font, but PDF viewers still vary in
    Arabic shaping/bidi behavior for mixed RTL text. Rendering the
    shaped run into a tiny transparent image makes the exported card
    behave like the live preview screenshot: letters stay connected,
    glyph order is stable, and the whole text block scales uniformly
    with the card form.
    """
    if not raw_text or not _has_arabic(raw_text):
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # pragma: no cover - optional dependency safety
        return None
    font_path = _font_path_for_arabic(bold=weight >= 700)
    if not os.path.isfile(font_path):
        return None
    font_size = max(1, int(round(size)))
    box_width = int(math.ceil(max_width)) if max_width and max_width > 0 else 0
    cache_key = (
        raw_text,
        font_size,
        color,
        int(weight),
        box_width,
        "rtl" if direction == "rtl" else "ltr",
        round(max(0.0, min(1.0, opacity)), 3),
    )
    cached = _arabic_text_image_cache.get(cache_key)
    if cached:
        return cached
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:  # pragma: no cover - corrupt font safety
        return None

    available_width = max(1, box_width - max(2, int(font_size * 0.16))) if box_width else 0
    fitted_raw = _fit_arabic_raw_text(raw_text, font=font, max_width=available_width) if available_width else raw_text
    shaped = _shape_arabic(fitted_raw)
    if not shaped:
        return None

    bbox = font.getbbox(shaped)
    text_w = max(1, int(math.ceil(bbox[2] - bbox[0])))
    text_h = max(1, int(math.ceil(bbox[3] - bbox[1])))
    pad_x = max(2, int(math.ceil(font_size * 0.12)))
    pad_y = max(2, int(math.ceil(font_size * 0.22)))
    width = max(box_width, text_w + pad_x * 2)
    height = max(int(math.ceil(font_size * 1.35)), text_h + pad_y * 2)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_x = width - pad_x - text_w if direction == "rtl" else pad_x
    draw_y = (height - text_h) / 2 - bbox[1]
    draw.text((draw_x, draw_y), shaped, font=font, fill=_rgba_from_pdf_color(color, opacity=opacity))
    buf = BytesIO()
    image.save(buf, format="PNG")
    result = (buf.getvalue(), width, height)
    # Keep the cache bounded; export jobs may process hundreds of cards.
    if len(_arabic_text_image_cache) > 512:
        _arabic_text_image_cache.clear()
    _arabic_text_image_cache[cache_key] = result
    return result


def _pdf_draw_arabic_text_image(
    pdf,
    raw_text: str,
    *,
    x: float,
    y: float,
    size: float,
    color: str,
    weight: int,
    max_width: float,
    direction: str,
    opacity: float,
    ch: float,
) -> bool:
    rendered = _build_arabic_text_image(
        raw_text,
        size=size,
        color=color,
        weight=weight,
        max_width=max_width,
        direction=direction,
        opacity=opacity,
    )
    if not rendered:
        return False
    from reportlab.lib.utils import ImageReader

    png_bytes, width, height = rendered
    pdf_y = ch - y - height
    pdf.drawImage(
        ImageReader(BytesIO(png_bytes)),
        x,
        pdf_y,
        width=width,
        height=height,
        mask="auto",
    )
    return True

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

_ENGINE_PROFILES: dict[str, dict[str, str]] = {
    "en_horizontal": {
        "orientation": "horizontal",
        "direction": "ltr",
        "credential_label_language": "english",
    },
    "en_vertical": {
        "orientation": "vertical",
        "direction": "ltr",
        "credential_label_language": "english",
    },
    "ar_horizontal": {
        "orientation": "horizontal",
        "direction": "rtl",
        "credential_label_language": "arabic",
    },
    "ar_vertical": {
        "orientation": "vertical",
        "direction": "rtl",
        "credential_label_language": "arabic",
    },
}

# Default show-flags. Mirror the same defaults the operations.py
# layout normaliser uses so a template that never set these explicitly
# still renders the expected elements.
_DEFAULT_SHOW = {
    "brand":    True,
    "title":    True,
    "username": True,
    "password": True,
    "qr":       True,
    "price":    False,
    "hotspot":  True,
    "validity": True,
    "serial":   True,
}


def normalize_render_engine(value: Any = None, layout: dict | None = None) -> str:
    """Return one of the four explicit card SVG engines.

    New templates store `render_engine` directly. Older templates are
    derived from their existing `card_orientation`, `text_direction`,
    and `credential_label_language` fields so compatibility is kept.
    """
    raw = str(value or "").strip().lower()
    if raw in _ENGINE_PROFILES:
        return raw
    layout = layout or {}
    orientation = str(layout.get("card_orientation") or "horizontal").strip().lower()
    orientation = "vertical" if orientation == "vertical" else "horizontal"
    direction = str(layout.get("text_direction") or "").strip().lower()
    label_language = str(layout.get("credential_label_language") or "").strip().lower()
    language = "ar" if direction == "rtl" or label_language == "arabic" else "en"
    return f"{language}_{orientation}"


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

    engine = normalize_render_engine(layout.get("render_engine"), layout)
    profile = _ENGINE_PROFILES[engine]
    orient = profile["orientation"]
    render_direction = profile["direction"]
    credential_label_language = profile["credential_label_language"]
    canvas_w, canvas_h = CANVAS_PORTRAIT if orient == "vertical" else CANVAS_LANDSCAPE

    show = _resolve_show_flags(layout)

    # ── Text + meta ──
    brand_text   = _override(overrides, "brand_name",   layout, "HobeRadius")
    title_text   = _override(overrides, "card_title",   layout, "بطاقة إنترنت")
    footer_text  = _override(overrides, "footer_text",  layout, "")
    hotspot_text = _override(overrides, "hotspot_address", layout, "")
    price_text   = _override(overrides, "price_text",   layout, "")
    validity_txt = _override(overrides, "validity_text", layout, "")

    positions = _resolve_positions(template, layout, (canvas_w, canvas_h), render_direction=render_direction)

    text_color    = _safe_hex(layout.get("text_color"), "#ffffff")
    accent_color  = _safe_hex(layout.get("accent_color"), "#f59e0b")
    surface_color = _safe_hex(layout.get("surface_color"), "#e8f7fb")
    credential_ink = _safe_hex(layout.get("credential_text_color"), "#0f172a")
    credential_label_color = _safe_hex(layout.get("credential_label_color"), "#64748b")
    username_surface = _safe_hex(layout.get("username_surface_color"), surface_color)
    password_surface = _safe_hex(layout.get("password_surface_color"), surface_color)
    credentials_surface_default = _boolish(layout.get("credential_background_enabled"), True)
    username_surface_enabled = _boolish(layout.get("username_surface_enabled"), credentials_surface_default)
    password_surface_enabled = _boolish(layout.get("password_surface_enabled"), credentials_surface_default)
    username_font_size = _optional_positive_float(layout.get("username_font_size"))
    password_font_size = _optional_positive_float(layout.get("password_font_size"))
    label_font_size = _optional_positive_float(layout.get("credential_label_font_size"))
    qr_color = _safe_hex(layout.get("qr_color"), "#0f172a")
    qr_background_color = _safe_hex(layout.get("qr_background_color"), "#ffffff")

    username, password, card_id = _extract_card_fields(card)
    uploaded_design = _is_uploaded_design(layout)

    elements: list[dict] = []

    # Accent bar — first so it sits beneath the text but above the bg.
    acc = positions["accent"]
    if not uploaded_design:
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

    heading_width = 0.78 if orient == "vertical" else 0.55

    if not uploaded_design and show["brand"] and brand_text:
        elements.append(_text_element(
            id="brand", text=brand_text, pos=positions["brand"],
            canvas=(canvas_w, canvas_h), color=text_color, weight=900,
            max_width_frac=heading_width,
            direction=render_direction,
        ))

    if not uploaded_design and show["title"] and title_text:
        elements.append(_text_element(
            id="title", text=title_text, pos=positions["title"],
            canvas=(canvas_w, canvas_h), color=text_color, weight=950,
            max_width_frac=heading_width,
            direction=render_direction,
        ))

    if show["username"] and username:
        elements.append(_pill_element(
            id="user", label=_credential_label("user", credential_label_language), value=username,
            pos=positions["user"], canvas=(canvas_w, canvas_h),
            surface_color=username_surface,
            surface_enabled=username_surface_enabled,
            ink=credential_ink,
            label_color=credential_label_color,
            value_font_size=username_font_size,
            label_font_size=label_font_size,
            label_direction="rtl" if credential_label_language == "arabic" else "ltr",
            show_label=not uploaded_design,
        ))

    if show["password"] and password:
        elements.append(_pill_element(
            id="pass", label=_credential_label("pass", credential_label_language), value=password,
            pos=positions["pass"], canvas=(canvas_w, canvas_h),
            surface_color=password_surface,
            surface_enabled=password_surface_enabled,
            ink=credential_ink,
            label_color=credential_label_color,
            value_font_size=password_font_size,
            label_font_size=label_font_size,
            is_password=True,
            label_direction="rtl" if credential_label_language == "arabic" else "ltr",
            show_label=not uploaded_design,
        ))

    if show["qr"]:
        qr = positions["qr"]
        payload = _qr_login_payload(layout, username, password, card_id)
        elements.append({
            "kind": "qr",
            "id": "qr",
            "payload": payload,
            "x": qr["x"] * canvas_w,
            "y": qr["y"] * canvas_h,
            "size": qr["size"] * canvas_w,
            "bg": qr_background_color,
            "fg": qr_color,
        })

    # Meta line: hotspot · price · validity · #serial
    meta_parts: list[str] = []
    if show["hotspot"]  and hotspot_text: meta_parts.append(hotspot_text)
    if show["price"]    and price_text:   meta_parts.append(price_text)
    if show["validity"] and validity_txt: meta_parts.append(validity_txt)
    if show["serial"]   and card_id:      meta_parts.append("#" + str(card_id))
    if not uploaded_design and meta_parts:
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
            "max_width": canvas_w * (0.80 if orient == "vertical" else 0.88),
            "direction": render_direction,
        })

    if not uploaded_design and footer_text:
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
            "max_width": canvas_w * (0.80 if orient == "vertical" else 0.88),
            "direction": render_direction,
        })

    return {
        "canvas": {"width": canvas_w, "height": canvas_h},
        "orientation": orient,
        "render_engine": engine,
        "render_direction": render_direction,
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
    uid = _svg_id("card", uuid.uuid4().hex[:10])
    bg_id = f"{uid}-bg"
    pattern_id = f"{uid}-pattern"
    clip_id = f"{uid}-clip"

    parts: list[str] = []
    # `direction="ltr"` is critical: the admin UI ships with
    # <html dir="rtl"> and every nested <text> inherits that direction
    # by default. In RTL, `text-anchor="start"` means the right edge of
    # the text box — so an LTR string like "HobeRadius" rendered at
    # x=60 walks off the LEFT side of the card and only the last few
    # characters stay visible inside the viewBox. Forcing ltr on the
    # SVG root (and on each <text> below) keeps card text laid out
    # left-to-right regardless of the document direction.
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" class="card-svg" '
        f'direction="ltr" '
        f'style="display:block;direction:ltr;overflow:visible;max-width:100%;max-height:100%">'
    )
    parts.append('<defs>')
    parts.extend(_svg_defs(bg, w, h, bg_id=bg_id, pattern_id=pattern_id))
    parts.append(f'<clipPath id="{clip_id}"><rect x="0" y="0" width="{w}" height="{h}" rx="{int(w*0.025)}" ry="{int(w*0.025)}"/></clipPath>')
    parts.append('</defs>')

    parts.append(f'<g clip-path="url(#{clip_id})">')
    parts.extend(_svg_background(bg, w, h, bg_id=bg_id, pattern_id=pattern_id))

    for el in model["elements"]:
        kind = el.get("kind")
        if kind == "rect":
            parts.append(_svg_rect(el))
        elif kind == "text":
            parts.append(_svg_text(el, uid=uid))
        elif kind == "pill":
            parts.append(_svg_pill(el, mask_password=mask_password, uid=uid))
        elif kind == "qr":
            parts.append(_svg_qr_placeholder(el))

    parts.append('</g>')
    parts.append('</svg>')
    return "".join(parts)


# ───────────────────────────────────────────────────────────────────
# PDF adapter
# ───────────────────────────────────────────────────────────────────

def render_card_pdf(pdf, model: dict, *, form_name: str,
                     expose_password: bool = True,
                     include_background: bool = True,
                     include_ids: set[str] | None = None,
                     exclude_ids: set[str] | None = None) -> None:
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
        _embed_arabic_font_marker(pdf, ch)
        if include_background:
            _pdf_background(pdf, model.get("background") or {}, cw, ch)
        for el in model["elements"]:
            el_id = str(el.get("id") or "")
            if include_ids is not None and el_id not in include_ids:
                continue
            if exclude_ids is not None and el_id in exclude_ids:
                continue
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


def _embed_arabic_font_marker(pdf, ch: float) -> None:
    """Embed Almarai even when Arabic is rasterized for perfect shaping."""
    if not _ensure_arabic_fonts():
        return
    try:
        pdf.saveState()
        pdf.setFont(PDF_FONT_ARABIC, 1)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.drawString(-1000, ch + 1000, "ا")
        pdf.restoreState()
    except Exception:
        try:
            pdf.restoreState()
        except Exception:
            pass


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


def _card_slot_fit(model: dict, *, slot_x: float, slot_y: float,
                   slot_width: float, slot_height: float) -> dict[str, float]:
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])
    fit = min(slot_width / max(cw, 1.0), slot_height / max(ch, 1.0))
    draw_w = cw * fit
    draw_h = ch * fit
    return {
        "scale": fit,
        "x": slot_x + (slot_width - draw_w) / 2.0,
        "y": slot_y + (slot_height - draw_h) / 2.0,
        "width": draw_w,
        "height": draw_h,
    }


def _uploaded_background_image_reader(bg: dict):
    image_url = str(bg.get("image_data_url") or "")
    source = str(bg.get("source") or "preset")
    if source != "image" or not image_url.startswith("data:image/") or ";base64," not in image_url:
        return None
    cached = _uploaded_background_reader_cache.get(image_url)
    if cached is not None:
        return cached
    try:
        from reportlab.lib.utils import ImageReader

        mime_part, encoded = image_url.split(";base64,", 1)
        image_bytes = base64.b64decode(encoded)
        if mime_part == "data:image/webp":
            image_bytes = _convert_bitmap_for_reportlab(image_bytes)
        if mime_part not in {"data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/webp"}:
            return None
        image = ImageReader(BytesIO(image_bytes))
        if len(_uploaded_background_reader_cache) > 8:
            _uploaded_background_reader_cache.clear()
        _uploaded_background_reader_cache[image_url] = image
        return image
    except Exception:
        return None


def model_uses_uploaded_background(model: dict) -> bool:
    """True when this card should use the dedicated uploaded-image export path."""
    return _uploaded_background_image_reader(model.get("background") or {}) is not None


def draw_uploaded_background_uniform(pdf, model: dict, *, slot_x: float, slot_y: float,
                                     slot_width: float, slot_height: float) -> bool:
    """Draw an uploaded card image directly on the PDF page.

    Uploaded images deliberately bypass the shared card form/XObject.
    Some PDF viewers and ReportLab form reuse paths can drop bitmap
    resources nested inside reusable forms; placing the uploaded bitmap
    on the page first and then drawing the text/QR forms above it keeps
    customer-uploaded artwork visible for 500, 1000, and larger batch
    exports.
    """
    from reportlab.lib import colors

    bg = model.get("background") or {}
    image = _uploaded_background_image_reader(bg)
    if image is None:
        return False
    fit = _card_slot_fit(
        model,
        slot_x=slot_x,
        slot_y=slot_y,
        slot_width=slot_width,
        slot_height=slot_height,
    )
    opacity = max(0.0, min(1.0, float(bg.get("image_opacity") or 1.0)))
    pdf.saveState()
    try:
        pdf.drawImage(
            image,
            fit["x"],
            fit["y"],
            width=fit["width"],
            height=fit["height"],
            preserveAspectRatio=False,
            mask="auto",
        )
        if opacity < 1:
            pdf.setFillColor(colors.Color(1, 1, 1, alpha=max(0, 1 - opacity)))
            pdf.rect(fit["x"], fit["y"], fit["width"], fit["height"], stroke=0, fill=1)
    finally:
        pdf.restoreState()
    return True


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

    image_url = bg.get("image_data_url") or ""
    source = str(bg.get("source") or "preset")
    if source == "image" and image_url.startswith("data:image/") and ";base64," in image_url:
        try:
            from io import BytesIO
            from reportlab.lib.utils import ImageReader

            mime_part, encoded = image_url.split(";base64,", 1)
            image_bytes = base64.b64decode(encoded)
            if mime_part == "data:image/webp":
                image_bytes = _convert_bitmap_for_reportlab(image_bytes)
            if mime_part in {"data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/webp"}:
                image = ImageReader(BytesIO(image_bytes))
                opacity = max(0.0, min(1.0, float(bg.get("image_opacity") or 1.0)))
                pdf.saveState()
                pdf.drawImage(image, 0, 0, width=cw, height=ch,
                              preserveAspectRatio=False, mask="auto")
                if opacity < 1:
                    pdf.setFillColor(colors.Color(1, 1, 1, alpha=max(0, 1 - opacity)))
                    pdf.rect(0, 0, cw, ch, stroke=0, fill=1)
                pdf.restoreState()
                return
        except Exception:
            pass

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


def _convert_bitmap_for_reportlab(raw: bytes) -> bytes:
    """Return PNG bytes for browser-friendly bitmap formats.

    The live SVG preview can embed any uploaded data URL the browser
    understands, including WebP. ReportLab is much stricter, so a WebP
    background looked correct in the designer but disappeared from the
    exported PDF. Converting through Pillow keeps the PDF path visually
    aligned with the browser preview without changing saved templates.
    """
    from PIL import Image

    with Image.open(BytesIO(raw)) as image:
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


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
    raw_text = str(el.get("text", ""))
    if not raw_text:
        return
    max_width = float(el.get("max_width") or 0)
    opacity = float(el.get("opacity", 1.0))
    if _has_arabic(raw_text):
        if _pdf_draw_arabic_text_image(
            pdf,
            raw_text,
            x=float(el["x"]),
            y=float(el["y"]),
            size=size,
            color=el.get("color", "#ffffff"),
            weight=weight,
            max_width=max_width,
            direction="rtl" if el.get("direction") == "rtl" else "ltr",
            opacity=opacity,
            ch=ch,
        ):
            return
    # Pick the right font for the text content and shape Arabic so
    # ReportLab gets the correctly-ordered presentation glyphs.
    font = _pick_pdf_font(raw_text, bold=weight >= 700)
    text = _shape_arabic(raw_text) if _has_arabic(raw_text) else raw_text
    pdf.setFont(font, size)
    color = _pdf_color(el.get("color", "#ffffff"))
    if opacity < 1.0:
        pdf.setFillColor(colors.Color(color.red, color.green, color.blue,
                                       alpha=max(0.0, min(1.0, opacity))))
    else:
        pdf.setFillColor(color)
    # SVG dominant-baseline="hanging" puts the text top at y. PDF's
    # drawString uses the baseline. Cap height ≈ 0.78 of font size for
    # Helvetica/Almarai, so drop y by that amount to align visually.
    baseline = ch - el["y"] - size * 0.78
    if max_width > 0:
        text = _shrink_to_fit(pdf, text, font, size, max_width)
    # RTL text is anchored at the right edge of its text box. Arabic is
    # additionally shaped/reordered above so ReportLab can draw it.
    if el.get("direction") == "rtl":
        right_edge = el["x"] + (max_width if max_width > 0 else
                                  pdf.stringWidth(text, font, size))
        pdf.drawRightString(right_edge, baseline, text)
    else:
        pdf.drawString(el["x"], baseline, text)


def _pdf_pill(pdf, el: dict, ch: float, *, expose_password: bool) -> None:
    """Draw the surface rect + label + value of a USER/PASS pill."""
    pdf_y = ch - el["y"] - el["height"]
    if el.get("surface_enabled", True):
        pdf.setFillColor(_pdf_color(el["surface"]))
        pdf.roundRect(el["x"], pdf_y, el["width"], el["height"],
                      el["height"] * 0.20, stroke=0, fill=1)
    label_box_x = el["x"] + el["padding_x"]
    label_box_w = max(1, el["width"] - 2 * el["padding_x"])
    if el.get("show_label", True):
        # Label (USER/PASS or Arabic labels).
        label_raw = str(el["label"])
        label_font = _pick_pdf_font(label_raw, bold=True)
        label_text = _shape_arabic(label_raw) if _has_arabic(label_raw) else label_raw
        label_size = max(float(el["label_font_size"]), 4.0)
        label_top = el["y"] + el["height"] * 0.18
        label_direction = "rtl" if el.get("label_direction") == "rtl" else "ltr"
        if _has_arabic(label_raw) and _pdf_draw_arabic_text_image(
            pdf,
            label_raw,
            x=label_box_x,
            y=label_top,
            size=label_size,
            color=el["label_color"],
            weight=900,
            max_width=label_box_w,
            direction=label_direction,
            opacity=1.0,
            ch=ch,
        ):
            pass
        else:
            pdf.setFont(label_font, label_size)
            pdf.setFillColor(_pdf_color(el["label_color"]))
            label_baseline = ch - label_top - label_size * 0.78
            if label_direction == "rtl":
                pdf.drawRightString(el["x"] + el["width"] - el["padding_x"],
                                    label_baseline,
                                    label_text)
            else:
                pdf.drawString(el["x"] + el["padding_x"],
                               label_baseline,
                               label_text)
    # Value (the real credential — masked if expose_password is False
    # and this pill carries the password).
    raw_value = el["value"]
    if el.get("is_password") and not expose_password:
        raw_value = "•" * min(max(len(raw_value), 6), 10)
    value_font = _pick_pdf_font(raw_value, bold=True)
    value_text = _shape_arabic(raw_value) if _has_arabic(raw_value) else raw_value
    value_size = max(float(el["value_font_size"]), 5.0)
    value_top = el["y"] + el["height"] * (0.46 if el.get("show_label", True) else 0.28)
    max_value_width = el["width"] - 2 * el["padding_x"]
    if _has_arabic(raw_value) and _pdf_draw_arabic_text_image(
        pdf,
        raw_value,
        x=el["x"] + el["padding_x"],
        y=value_top,
        size=value_size,
        color=el["ink"],
        weight=900,
        max_width=max_value_width,
        direction="rtl",
        opacity=1.0,
        ch=ch,
    ):
        return
    pdf.setFont(value_font, value_size)
    pdf.setFillColor(_pdf_color(el["ink"]))
    if max_value_width > 0:
        value_text = _shrink_to_fit(pdf, value_text, value_font,
                                     value_size, max_value_width)
    pdf.drawString(el["x"] + el["padding_x"],
                   ch - value_top - value_size * 0.78,
                   value_text)


def _pdf_qr(pdf, el: dict, ch: float) -> None:
    """Draw a QR symbol using the same QrCodeWidget as the SVG path.

    Tight white panel: `barBorder=0` strips the QrCodeWidget's built-in
    4-module quiet zone (which used to leave a large empty white band
    around the actual QR pattern). The remaining 4% inner padding plus
    the white background rectangle itself give the scanner enough
    quiet area without making the panel visually oversized.
    """
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics import renderPDF
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    size = float(el["size"])
    pdf_y_top = ch - el["y"]  # top of the QR box in PDF coords
    pdf_y_bottom = pdf_y_top - size

    # Rounded background sits at the model's allocated size.
    pdf.setFillColor(_pdf_color(el.get("bg", "#ffffff")))
    pdf.roundRect(el["x"], pdf_y_bottom, size, size, size * 0.10,
                  stroke=0, fill=1)

    payload = str(el.get("payload") or "SAMPLE")
    try:
        widget = QrCodeWidget(payload, barBorder=0)
        try:
            widget.barFillColor = _pdf_color(el.get("fg", "#0f172a"))
            widget.barStrokeColor = _pdf_color(el.get("fg", "#0f172a"))
        except Exception:
            pass
        bounds = widget.getBounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        inner = size * 0.92  # 4% padding each side — visually tight
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
    """Legacy no-op kept for backward compatibility.

    Pre-Almarai this helper stripped non-Latin-1 characters so the
    default Helvetica font wouldn't crash on Arabic glyphs. Now that
    the renderer registers Almarai and shapes Arabic via
    arabic-reshaper + python-bidi, the strip is no longer needed and
    actively harmful (it would drop the very glyphs the new path
    knows how to render). The helper just coerces to str.
    """
    return str(value or "")


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


def _resolve_positions(
    template: dict,
    layout: dict,
    canvas: tuple[int, int],
    *,
    render_direction: str = "ltr",
) -> dict[str, dict[str, float]]:
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

    orientation = "vertical" if canvas[1] > canvas[0] else "horizontal"
    positions = _engine_default_positions(render_direction, orientation=orientation)

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

    qr_size_pct = _optional_positive_float(layout.get("qr_size_pct"))
    if qr_size_pct is not None:
        positions["qr"]["size"] = max(0.08, min(0.48, qr_size_pct / 100.0))

    return positions


def _engine_default_positions(
    render_direction: str,
    *,
    orientation: str = "horizontal",
) -> dict[str, dict[str, float]]:
    """Return default element positions for the selected language engine.

    Arabic engines are not just text-direction variants; the whole
    default composition is flipped so QR/barcode sits on the left and
    Arabic copy/pills sit on the right. Custom dragged coordinates are
    applied after these defaults and are treated as absolute positions
    in the active engine, so dragging never gets mirrored twice.
    """
    positions = {key: dict(value) for key, value in _DEFAULT_POSITIONS.items()}
    if orientation == "vertical":
        # Portrait cards need their own proportions. Reusing the
        # landscape text sizes makes headings huge and crops the lower
        # footer area once the whole card is scaled into a print cell.
        positions.update({
            "accent": {"x": 0.06, "y": 0.045, "width": 0.88, "height": 0.012},
            "brand":  {"x": 0.07, "y": 0.12, "size": 0.046},
            "title":  {"x": 0.07, "y": 0.20, "size": 0.056},
            "qr":     {"x": 0.62, "y": 0.30, "size": 0.24},
            "user":   {"x": 0.07, "y": 0.50, "width": 0.56, "height": 0.078},
            "pass":   {"x": 0.07, "y": 0.61, "width": 0.56, "height": 0.078},
            "meta":   {"x": 0.07, "y": 0.80, "size": 0.028},
            "footer": {"x": 0.07, "y": 0.88, "size": 0.027},
        })
    if render_direction != "rtl":
        return positions
    width_by_key = {
        "brand": 0.78 if orientation == "vertical" else 0.55,
        "title": 0.78 if orientation == "vertical" else 0.55,
        "meta": 0.80 if orientation == "vertical" else 0.88,
        "footer": 0.80 if orientation == "vertical" else 0.88,
    }
    for key, pos in positions.items():
        span = width_by_key.get(key) or pos.get("width") or pos.get("size") or 0.0
        if span:
            pos["x"] = max(0.0, min(1.0, 1.0 - float(pos.get("x", 0.0)) - float(span)))
    return positions


def _resolve_show_flags(layout: dict) -> dict[str, bool]:
    return {
        key: _boolish(layout.get(f"show_{key}"), default)
        for key, default in _DEFAULT_SHOW.items()
    }


def _credential_label(kind: str, language: str) -> str:
    if str(language or "").lower() == "arabic":
        return "اسم المستخدم" if kind == "user" else "كلمة المرور"
    return "USER" if kind == "user" else "PASS"


def _background(layout: dict) -> dict:
    image_url = str(layout.get("background_image_data_url") or "")
    has_image = image_url.startswith("data:image/")
    raw_source = str(
        layout.get("background_source")
        or layout.get("background_style")
        or ""
    ).strip().lower()
    if raw_source in {"image", "stored_image", "photo", "upload", "uploaded"}:
        source = "image"
    elif raw_source in {"preset", "system", "graphics", "generated"}:
        source = "preset"
    elif raw_source == "gradient":
        source = "image" if has_image else "preset"
    else:
        source = "image" if has_image else "preset"
    if source == "image" and not has_image:
        source = "preset"
    return {
        "source":         source,
        "gradient_start": _safe_hex(layout.get("gradient_start"), "#0f172a"),
        "gradient_end":   _safe_hex(layout.get("gradient_end"),   "#22a7bd"),
        "pattern":        str(layout.get("pattern_style") or "signal") if source == "preset" else "clean",
        "image_data_url": image_url if source == "image" and has_image else "",
        "image_opacity":  1.0 if source == "image" else max(0.0, min(1.0, _float(layout.get("image_opacity"), 0.82))),
    }


def _is_uploaded_design(layout: dict) -> bool:
    bg = _background(layout)
    return bg.get("source") == "image" and bool(bg.get("image_data_url"))


def _qr_login_payload(layout: dict, username: str, password: str, card_id: str) -> str:
    user = str(username or card_id or "SAMPLE")
    secret = str(password or "")
    host = str(
        layout.get("login_url")
        or layout.get("hotspot_address")
        or layout.get("hotspot_url")
        or ""
    ).strip()
    if not host:
        return user
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", host):
        host = "http://" + host
    base = host.rstrip("/")
    if not base.lower().endswith("/login"):
        base += "/login"
    return base + "?" + urlencode({"username": user, "password": secret})


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
                   color: str, weight: int, max_width_frac: float,
                   direction: str = "ltr") -> dict:
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
        "direction": direction,
    }


def _pill_element(*, id: str, label: str, value: str, pos: dict,
                   canvas: tuple[int, int], surface_color: str,
                   surface_enabled: bool = True,
                   ink: str = "#0f172a",
                   label_color: str = "#64748b",
                   value_font_size: float | None = None,
                   label_font_size: float | None = None,
                   is_password: bool = False,
                   label_direction: str = "ltr",
                   show_label: bool = True) -> dict:
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
        "surface_enabled": surface_enabled,
        "ink": ink,
        "label_color": label_color,
        "is_password": is_password,
        "show_label": show_label,
        "label_direction": label_direction,
        "value_direction": "ltr",
        "value_font_size": value_font_size or height * 0.52,
        "label_font_size": label_font_size or height * 0.30,
        "padding_x": height * 0.32,
    }


# ───────────────────────────────────────────────────────────────────
# Internal helpers — SVG output
# ───────────────────────────────────────────────────────────────────

def _svg_defs(bg: dict, w: int, h: int, *, bg_id: str, pattern_id: str) -> Iterable[str]:
    yield (
        f'<linearGradient id="{bg_id}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{_xml(bg.get("gradient_start", "#0f172a"))}"/>'
        f'<stop offset="100%" stop-color="{_xml(bg.get("gradient_end", "#22a7bd"))}"/>'
        f'</linearGradient>'
    )
    # Decorative pattern overlays.
    pattern = bg.get("pattern") or "signal"
    if pattern == "grid":
        step = max(int(w * 0.045), 8)
        yield (
            f'<pattern id="{pattern_id}" patternUnits="userSpaceOnUse" '
            f'width="{step}" height="{step}">'
            f'<path d="M{step} 0 L0 0 0 {step}" fill="none" '
            f'stroke="rgba(255,255,255,.45)" stroke-width="1"/>'
            f'</pattern>'
        )
    elif pattern == "wave":
        # Two faint radial highlights, drawn as a single SVG pattern.
        yield (
            f'<radialGradient id="{pattern_id}" cx="20%" cy="30%" r="55%">'
            f'<stop offset="0%"  stop-color="rgba(255,255,255,.30)"/>'
            f'<stop offset="60%" stop-color="rgba(255,255,255,0)"/>'
            f'</radialGradient>'
        )
    elif pattern == "signal":
        # Vertical signal bars at the bottom 30 % of the card.
        yield (
            f'<pattern id="{pattern_id}" patternUnits="userSpaceOnUse" '
            f'width="{max(int(w*0.025),6)}" height="{h}">'
            f'<rect x="0" y="{int(h*0.7)}" width="{max(int(w*0.005),2)}" '
            f'height="{int(h*0.3)}" fill="rgba(255,255,255,.40)"/>'
            f'</pattern>'
        )
    # "clean" emits no overlay.


def _svg_background(bg: dict, w: int, h: int, *, bg_id: str, pattern_id: str) -> Iterable[str]:
    image_url = bg.get("image_data_url") or ""
    if str(bg.get("source") or "preset") == "image" and image_url:
        opacity = bg.get("image_opacity", 1.0)
        yield (
            f'<image href="{_xml(image_url)}" x="0" y="0" '
            f'width="{w}" height="{h}" '
            f'preserveAspectRatio="xMidYMid slice" opacity="{opacity:.2f}"/>'
        )
        return

    yield f'<rect x="0" y="0" width="{w}" height="{h}" fill="url(#{bg_id})"/>'
    # Optional background image (kept inside the clip-path).
    pattern = bg.get("pattern") or "signal"
    if pattern in {"grid", "signal"}:
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="url(#{pattern_id})" opacity="0.45"/>'
        )
    elif pattern == "wave":
        yield (
            f'<rect x="0" y="0" width="{w}" height="{h}" '
            f'fill="url(#{pattern_id})"/>'
        )


def _svg_rect(el: dict) -> str:
    return (
        f'<rect x="{el["x"]:.1f}" y="{el["y"]:.1f}" '
        f'width="{el["width"]:.1f}" height="{el["height"]:.1f}" '
        f'rx="{el.get("rx", 0):.1f}" ry="{el.get("rx", 0):.1f}" '
        f'fill="{_xml(el.get("fill", "#fff"))}"/>'
    )


def _svg_text(el: dict, *, uid: str) -> str:
    weight = el.get("weight", 700)
    opacity = el.get("opacity", 1.0)
    direction = "rtl" if el.get("direction") == "rtl" else "ltr"
    text = str(el.get("text", ""))
    is_arabic = _has_arabic(text)
    display_text = _shape_arabic(text) if is_arabic else text
    # This SVG is the source snapshot for PDF export. Do not rely on
    # the SVG rasterizer to shape Arabic; emit visual glyph order here
    # and keep the logical text only in data-original.
    svg_direction = "ltr" if is_arabic else direction
    unicode_bidi = "bidi-override" if is_arabic else "embed"
    max_width = float(el.get("max_width") or 0)
    x = float(el["x"])
    anchor = "start"
    if direction == "rtl":
        x = x + max_width if max_width > 0 else x
        anchor = "end"
    clip_id = _svg_id(f"{uid}-clip-text", el.get("id", "text"))
    clip_rect = ""
    clip_attr = ""
    if max_width > 0:
        clip_x = float(el["x"])
        clip_h = float(el["size"]) * 1.35
        clip_rect = (
            f'<clipPath id="{clip_id}">'
            f'<rect x="{clip_x:.1f}" y="{float(el["y"]):.1f}" '
            f'width="{max_width:.1f}" height="{clip_h:.1f}"/>'
            f'</clipPath>'
        )
        clip_attr = f' clip-path="url(#{clip_id})"'
    return (
        f'{clip_rect}'
        f'<text x="{x:.1f}" y="{el["y"]:.1f}" '
        f'{clip_attr} '
        f'data-original="{_xml(text)}" '
        f'data-render-direction="{direction}" '
        f'direction="{svg_direction}" unicode-bidi="{unicode_bidi}" '
        f'font-family="\'Cairo\', \'Almarai\', \'Noto Kufi Arabic\', Tahoma, Arial, sans-serif" '
        f'font-size="{el["size"]:.1f}" font-weight="{weight}" '
        f'fill="{_xml(el.get("color", "#fff"))}" opacity="{opacity:.2f}" '
        f'dominant-baseline="hanging" text-anchor="{anchor}" xml:space="preserve">'
        f'{_xml(display_text)}'
        f'</text>'
    )


def _svg_pill(el: dict, *, mask_password: bool, uid: str) -> str:
    value = str(el["value"])
    if mask_password and el.get("is_password"):
        value = "•" * min(max(len(value), 6), 10)
    display_value = _shape_arabic(value) if _has_arabic(value) else value
    label_size = el["label_font_size"]
    value_size = el["value_font_size"]
    pad = el["padding_x"]
    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    label_y = y + h * 0.36
    show_label = bool(el.get("show_label", True))
    value_y = y + h * (0.72 if show_label else 0.54)
    label_dir = "rtl" if el.get("label_direction") == "rtl" else "ltr"
    label_text = str(el.get("label", ""))
    label_is_arabic = _has_arabic(label_text)
    display_label = _shape_arabic(label_text) if label_is_arabic else label_text
    svg_label_dir = "ltr" if label_is_arabic else label_dir
    label_unicode_bidi = "bidi-override" if label_is_arabic else "embed"
    label_x = x + w - pad if label_dir == "rtl" else x + pad
    label_anchor = "end" if label_dir == "rtl" else "start"
    clip_id = _svg_id(f"{uid}-clip-pill", el.get("id", "pill"))
    text_clip = (
        f'<clipPath id="{clip_id}">'
        f'<rect x="{x+pad:.1f}" y="{y:.1f}" width="{max(w-2*pad, 1):.1f}" height="{h:.1f}"/>'
        f'</clipPath>'
    )
    surface = (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{h*0.20:.1f}" ry="{h*0.20:.1f}" '
        f'fill="{_xml(el["surface"])}" opacity="0.95"/>'
        if el.get("surface_enabled", True) else ""
    )
    return (
        f'<g class="card-pill">'
        f'{text_clip}'
        f'{surface}'
        f'{f"""<text x="{label_x:.1f}" y="{label_y:.1f}" clip-path="url(#{clip_id})" '
        f'data-original="{_xml(label_text)}" '
        f'data-render-direction="{label_dir}" '
        f'direction="{svg_label_dir}" unicode-bidi="{label_unicode_bidi}" '
        f'font-family="\'Cairo\', \'Almarai\', \'Noto Kufi Arabic\', Tahoma, Arial, sans-serif" '
        f'font-size="{label_size:.1f}" font-weight="900" '
        f'fill="{_xml(el["label_color"])}" '
        f'dominant-baseline="middle" text-anchor="{label_anchor}" xml:space="preserve">'
        f'{_xml(display_label)}</text>""" if show_label else ""}'
        f'<text x="{x+pad:.1f}" y="{value_y:.1f}" clip-path="url(#{clip_id})" '
        f'direction="ltr" '
        f'font-family="\'Menlo\', \'Consolas\', monospace" '
        f'font-size="{value_size:.1f}" font-weight="900" '
        f'fill="{_xml(el["ink"])}" '
        f'dominant-baseline="middle" text-anchor="start" xml:space="preserve">'
        f'{_xml(display_value)}</text>'
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
    # 4 % inner padding to match the PDF adapter — keeps the white
    # panel hugging the QR symbol instead of floating around it.
    pad = size * 0.04
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


def _optional_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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


def _svg_id(prefix: str, value: Any) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "x")).strip("-") or "x"
    return f"{prefix}-{raw}"
