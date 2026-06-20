# -*- coding: utf-8 -*-
"""FAITHFUL card previews — rendered the way the live app renders them:
the production SVG (`render_card_svg`) drawn in headless Chrome with the
shipped Cairo (+ Almarai fallback) @font-face. This matches the browser
preview exactly — Cairo font, correct RTL bidi, per-glyph fallback for the
few Arabic presentation forms Cairo doesn't ship — instead of the PDF-raster
path (which, without libraqm, falls back to a non-Cairo system font and was
misleading the reviewer).

Outputs:
  preview/card_heading_modes.png  — 4 modes (vertical/horizontal × AR/EN),
                                    long wrapping titles, no truncation.
  preview/card_footer_preview.png — footer/tagline lifted with clearance
                                    (cafe+clinic landscape, cafe vertical).
Run:  python tools/capture_card_footer.py
"""
from __future__ import annotations

import base64
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from app.radius.services.operations import _template_layout
from app.radius.services.card_renderer import build_card_render_model, render_card_svg

FONTS = os.path.join(REPO, "app", "static", "fonts")
TAGLINE = "استمتع بقهوتك على إنترنت سريع"
LONG_AR = "شبكة المقهى للضيوف - واي فاي مجاني"
LONG_EN = "Cafe Guest Wi-Fi Free Internet Access"

# (preset, engine, w_mm, h_mm, title|None)
HEADING_MODES = [
    ("cafe_mocha", "ar_vertical",   54, 85, LONG_AR),
    ("cafe_mocha", "en_vertical",   54, 85, LONG_EN),
    ("cafe_mocha", "ar_horizontal", 85, 54, LONG_AR),
    ("cafe_mocha", "en_horizontal", 85, 54, LONG_EN),
]
FOOTER_CARDS = [
    ("cafe_mocha",  "ar_horizontal", 85, 54, None),
    ("clinic_calm", "ar_horizontal", 85, 54, None),
    ("cafe_mocha",  "ar_vertical",   54, 85, None),
]


def _font_face(family: str, filename: str, weight: int) -> str:
    path = os.path.join(FONTS, filename)
    b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
    return (f"@font-face{{font-family:'{family}';font-weight:{weight};"
            f"font-style:normal;font-display:block;"
            f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}")


def _fonts_css() -> str:
    return "".join([
        _font_face("Cairo", "Cairo-Regular.ttf", 400),
        _font_face("Cairo", "Cairo-Bold.ttf", 700),
        _font_face("Cairo", "Cairo-Black.ttf", 900),
        _font_face("Almarai", "Almarai-Bold.ttf", 700),
        _font_face("Almarai", "Almarai-ExtraBold.ttf", 800),
    ])


def _card_svg(preset: str, engine: str, w_mm: int, h_mm: int,
              title: str | None) -> str:
    extra = {"footer_text": TAGLINE}
    if title is not None:
        extra["card_title"] = title
        extra["brand_name"] = ("Cafe Hotspot" if engine.startswith("en")
                               else "مقهى الشبكة")
    layout = _template_layout({
        "design_preset": preset,
        "card_width_mm": w_mm, "card_height_mm": h_mm,
        "card_orientation": "vertical" if h_mm > w_mm else "horizontal",
        **extra,
    })
    # _template_layout does NOT carry through render_engine, so the engine
    # would default to en_* (LTR) and Arabic cards render un-mirrored. Set it
    # on the resolved layout so the AR engines (RTL) mirror the layout.
    layout["render_engine"] = engine
    template = {"id": 1, "name": "t",
                "orientation": "portrait" if h_mm > w_mm else "landscape",
                "layout_json": layout}
    model = build_card_render_model(
        template, {"id": "128", "username": "7772", "password": "Pw_9152"})
    return render_card_svg(model, mask_password=False)


def _page_html(cards: list, columns: int) -> str:
    cells = []
    for (preset, engine, w_mm, h_mm, title) in cards:
        svg = _card_svg(preset, engine, w_mm, h_mm, title)
        disp_w = 360 if w_mm >= h_mm else 250  # landscape wider than vertical
        cells.append(f'<div class="cell" style="width:{disp_w}px">{svg}</div>')
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        + _fonts_css() +
        "html,body{margin:0;background:#0b1220}"
        ".grid{display:grid;grid-template-columns:" + ("1fr " * columns)
        + ";gap:26px;padding:26px;width:max-content}"
        ".cell svg{display:block;width:100%;height:auto;"
        "border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.45)}"
        "</style></head><body><div class='grid'>"
        + "".join(cells) + "</div></body></html>"
    )


def _shoot(html: str, out_path: str) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(device_scale_factor=2)
        page.set_content(html, wait_until="load")
        page.evaluate("async () => { await document.fonts.ready; }")
        page.query_selector(".grid").screenshot(path=out_path)
        browser.close()


def main() -> None:
    os.makedirs(os.path.join(REPO, "preview"), exist_ok=True)
    p2 = os.path.join(REPO, "preview", "card_heading_modes.png")
    _shoot(_page_html(HEADING_MODES, columns=2), p2)
    print("PREVIEW:", p2)
    p1 = os.path.join(REPO, "preview", "card_footer_preview.png")
    _shoot(_page_html(FOOTER_CARDS, columns=1), p1)
    print("PREVIEW:", p1)


if __name__ == "__main__":
    main()
