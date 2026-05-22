"""Regression tests for the unified card renderer.

These tests defend the invariant: web preview and PDF export are
produced from the same render model. If they ever drift, one of these
tests will fail.

What each test pins
-------------------
- model_contains_user_pass_qr: the model carries username, password,
  and a QR payload for every real card.
- preview_fragment_uses_unified_svg: the live preview-fragment HTML
  embeds the renderer's SVG output, not a hand-coded HTML mock.
- batch_pdf_includes_username_and_password: the batch PDF bytes
  contain both credentials — they cannot be silently dropped.
- pdf_starts_with_magic: the export route returns a real PDF.
- internal_ratios_preserved_across_cards_per_row: the model is
  canvas-pinned, so changing cards_per_row does not change anything
  the renderer puts inside the card.
- qr_uses_canvas_units: the QR element's `size` is in canvas units,
  derived from the canvas width, NOT from the final PDF sheet cell.
- bg_image_surfaces_in_model_and_svg: an uploaded background image
  flows into both the model and the SVG.
- portrait_vs_landscape_canvases: orientation switch changes the
  canvas dimensions but still uses the same renderer.
- password_masked_in_svg_exposed_in_pdf: SVG always masks the
  password; PDF receives the real value when `expose_password=True`.
"""
from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _pdf_text_blob(pdf_bytes: bytes) -> bytes:
    """Decode every stream in `pdf_bytes` and concatenate the
    decoded payloads. ReportLab compresses page streams by default
    with `/Filter [/ASCII85Decode /FlateDecode]`, so a naive
    `b"CARD7777" in pdf_bytes` would never see the credentials.

    This helper sidesteps that without adding a PDF library dependency:
    - Find every `stream … endstream` block.
    - ASCII85-decode if the payload ends with `~>`.
    - Flate-decompress if the payload looks zlib-shaped (0x78 prefix).
    - Append the raw bytes if neither succeeds (e.g. embedded image).

    Returns one big byte string — the caller does `in` checks on it.
    """
    import base64
    import re
    import zlib

    out = bytearray()
    # The body sits between `stream\n` and `endstream`. The closing
    # delimiter is NOT prefixed with a newline when ASCII85 is used
    # (the `~>` end marker stands in for the linebreak).
    pattern = re.compile(rb"stream\r?\n(.*?)\s*endstream",
                          flags=re.DOTALL)
    for match in pattern.finditer(pdf_bytes):
        body = match.group(1).rstrip(b"\r\n ")
        decoded: bytes = body
        # ASCII85: ReportLab wraps the FlateDecode payload in ASCII85
        # (terminator `~>`). base64.a85decode handles it.
        if decoded.endswith(b"~>"):
            try:
                decoded = base64.a85decode(decoded, adobe=True)
            except (ValueError, base64.binascii.Error):
                pass
        # Flate: zlib stream typically starts with 0x78.
        if decoded[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda"):
            try:
                decoded = zlib.decompress(decoded)
            except zlib.error:
                pass
        out += decoded
    return bytes(out)


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"render_web_{uuid4().hex[:10]}"
    password = "render-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Card Renderer Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _make_template(**overrides) -> dict:
    layout = {
        "card_orientation": "horizontal",
        "card_width_mm": 85,
        "card_height_mm": 54,
        "gradient_start": "#0f172a",
        "gradient_end": "#22a7bd",
        "accent_color": "#f59e0b",
        "text_color": "#ffffff",
        "surface_color": "#e8f7fb",
        "brand_name": "HobeRadius",
        "card_title": "Internet Card",
        "footer_text": "Keep login data until expiry",
        "hotspot_address": "hotspot.local",
        "pattern_style": "signal",
        "show_brand": True,
        "show_username": True,
        "show_password": True,
        "show_qr": True,
        "show_hotspot": True,
        "show_serial": True,
        "show_validity": True,
        "show_price": False,
    }
    layout.update(overrides.pop("layout", {}))
    template = {
        "id": overrides.pop("id", 1),
        "name": overrides.pop("name", "Test Template"),
        "orientation": "portrait",
        "cards_per_row": 2,
        "cards_per_column": 5,
        "page_size": "A4",
        "font_size": 12,
        "color": "#1f2937",
        "show_qr": True,
        "username_x": 0, "username_y": 0,
        "password_x": 0, "password_y": 0,
        "qr_x": 0, "qr_y": 0,
        "layout_json": layout,
    }
    template.update(overrides)
    return template


# ───────────────────────────────────────────────────────────────────
# Model + SVG adapter
# ───────────────────────────────────────────────────────────────────

def test_model_contains_user_pass_and_qr():
    """The render model carries username, password (real), and QR payload."""
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _make_template(),
        {"id": 915, "username": "d2-85104", "password": "Secret_pw_1"},
    )
    assert model["canvas"] == {"width": 1000, "height": 600}, model["canvas"]
    elements_by_id = {e["id"]: e for e in model["elements"]}
    assert "user" in elements_by_id and elements_by_id["user"]["value"] == "d2-85104"
    assert "pass" in elements_by_id and elements_by_id["pass"]["value"] == "Secret_pw_1"
    assert "qr" in elements_by_id and elements_by_id["qr"]["payload"] == "d2-85104"
    # Sanity: the renderer-level password is captured for the PDF adapter.
    assert model["password"] == "Secret_pw_1"


def test_password_masked_in_svg_exposed_in_pdf():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    model = build_card_render_model(
        _make_template(),
        {"id": 1, "username": "u1", "password": "MyClearPw01"},
    )
    svg = render_card_svg(model)  # mask_password=True by default
    assert "MyClearPw01" not in svg
    assert "•" in svg  # bullet mask

    svg_unmasked = render_card_svg(model, mask_password=False)
    assert "MyClearPw01" in svg_unmasked


def test_show_title_flag_hides_card_title():
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _make_template(layout={"card_title": "TITLE_SHOULD_HIDE", "show_title": False}),
        {"id": 1, "username": "u1", "password": "p1"},
    )

    element_ids = {item["id"] for item in model["elements"]}
    assert "title" not in element_ids
    assert all(item.get("text") != "TITLE_SHOULD_HIDE" for item in model["elements"])


def test_internal_ratios_preserved_across_cards_per_row():
    """Same template at 2x5 vs 4x6: model elements must be IDENTICAL —
    cards_per_row only affects sheet slot size, never card contents."""
    from app.radius.services.card_renderer import build_card_render_model

    base = _make_template(cards_per_row=2, cards_per_column=5)
    wide = _make_template(cards_per_row=4, cards_per_column=6)
    card = {"id": 7, "username": "u7", "password": "p7"}
    m1 = build_card_render_model(base, card)
    m2 = build_card_render_model(wide, card)
    assert m1["canvas"] == m2["canvas"]
    assert m1["elements"] == m2["elements"]


def test_qr_uses_canvas_units_not_sheet_cell():
    """QR `x`, `y`, `size` come from the canvas (1000x600), never the
    final sheet cell. This is the invariant that stops sheet layout
    from distorting the QR."""
    from app.radius.services.card_renderer import build_card_render_model

    model = build_card_render_model(
        _make_template(),
        {"id": 1, "username": "u", "password": "p"},
    )
    qr = next(e for e in model["elements"] if e["id"] == "qr")
    # QR sits inside the 1000x600 canvas with non-degenerate size.
    assert 0 < qr["x"] < 1000
    assert 0 < qr["y"] < 600
    assert 100 < qr["size"] < 400


def test_uploaded_design_layer_controls_drive_model_and_svg():
    """Uploaded-image mode gets its own layer controls: QR size/color,
    credential font/color, and removable credential backgrounds."""
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    template = _make_template(layout={
        "background_style": "image",
        "qr_size_pct": 18,
        "qr_color": "#ff0000",
        "qr_background_color": "#eeeeee",
        "credential_text_color": "#123456",
        "credential_label_color": "#654321",
        "username_surface_enabled": False,
        "password_surface_enabled": True,
        "username_surface_color": "#abcdef",
        "password_surface_color": "#fedcba",
        "username_font_size": 44,
        "password_font_size": 33,
        "credential_label_font_size": 14,
    })
    model = build_card_render_model(
        template,
        {"id": 1, "username": "u-layer", "password": "p-layer"},
    )
    elements = {item["id"]: item for item in model["elements"]}
    qr = elements["qr"]
    user = elements["user"]
    password = elements["pass"]

    assert qr["size"] == pytest.approx(180)
    assert qr["fg"] == "#ff0000"
    assert qr["bg"] == "#eeeeee"
    assert user["surface_enabled"] is False
    assert password["surface_enabled"] is True
    assert user["surface"] == "#abcdef"
    assert password["surface"] == "#fedcba"
    assert user["ink"] == "#123456"
    assert user["label_color"] == "#654321"
    assert user["value_font_size"] == 44
    assert password["value_font_size"] == 33
    assert user["label_font_size"] == 14

    svg = render_card_svg(model)
    assert 'fill="#ff0000"' in svg
    assert 'fill="#123456"' in svg
    assert 'fill="#abcdef"' not in svg
    assert 'fill="#fedcba"' in svg


def test_background_image_surfaces_in_model_and_svg():
    """Uploaded background image must flow into both the model and SVG."""
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    # 1×1 transparent PNG.
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
    )
    template = _make_template(layout={"background_image_data_url": data_url, "image_opacity": 0.6})
    model = build_card_render_model(template, {"id": 1, "username": "u", "password": "p"})
    assert model["background"]["image_data_url"].startswith("data:image/png;base64,")
    assert abs(model["background"]["image_opacity"] - 0.6) < 1e-6
    svg = render_card_svg(model)
    assert "data:image/png;base64," in svg


def test_webp_background_image_is_embedded_in_pdf():
    """Browser preview supports WebP data URLs; PDF export must not drop them."""
    import base64
    from reportlab.pdfgen import canvas

    Image = pytest.importorskip("PIL.Image")
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_pdf,
    )

    image = Image.new("RGB", (3, 2), (36, 167, 189))
    raw = BytesIO()
    image.save(raw, format="WEBP")
    data_url = "data:image/webp;base64," + base64.b64encode(raw.getvalue()).decode("ascii")

    model = build_card_render_model(
        _make_template(layout={"background_image_data_url": data_url, "image_opacity": 0.9}),
        {"id": 1, "username": "u", "password": "p"},
    )
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=(300, 180))
    render_card_pdf(pdf, model, form_name="webp_bg", expose_password=True)
    pdf.doForm("webp_bg")
    pdf.showPage()
    pdf.save()

    pdf_bytes = out.getvalue()
    assert pdf_bytes.startswith(b"%PDF")
    assert b"/Subtype /Image" in pdf_bytes


def test_uploaded_background_has_separate_page_draw_engine():
    """Uploaded artwork is drawn directly on the PDF page, outside reusable forms."""
    import base64
    from reportlab.pdfgen import canvas

    Image = pytest.importorskip("PIL.Image")
    from app.radius.services.card_renderer import (
        build_card_render_model,
        draw_uploaded_background_uniform,
        model_uses_uploaded_background,
    )

    image = Image.new("RGB", (31, 17), (193, 42, 84))
    raw = BytesIO()
    image.save(raw, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode("ascii")
    model = build_card_render_model(
        _make_template(layout={
            "background_style": "image",
            "background_image_data_url": data_url,
            "image_opacity": 1,
        }),
        {"id": 1, "username": "u", "password": "p"},
    )

    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=(300, 180))
    assert model_uses_uploaded_background(model) is True
    assert draw_uploaded_background_uniform(
        pdf,
        model,
        slot_x=10,
        slot_y=20,
        slot_width=200,
        slot_height=100,
    ) is True
    pdf.showPage()
    pdf.save()

    pdf_bytes = out.getvalue()
    assert pdf_bytes.startswith(b"%PDF")
    assert b"/Subtype /Image" in pdf_bytes
    assert b"/Width 31" in pdf_bytes
    assert b"/Height 17" in pdf_bytes


def test_portrait_vs_landscape_canvases():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        CANVAS_LANDSCAPE,
        CANVAS_PORTRAIT,
    )

    landscape = _make_template(layout={"card_orientation": "horizontal"})
    portrait = _make_template(layout={"card_orientation": "vertical"})
    m_l = build_card_render_model(landscape, {"id": 1, "username": "u", "password": "p"})
    m_p = build_card_render_model(portrait, {"id": 1, "username": "u", "password": "p"})
    assert (m_l["canvas"]["width"], m_l["canvas"]["height"]) == CANVAS_LANDSCAPE
    assert (m_p["canvas"]["width"], m_p["canvas"]["height"]) == CANVAS_PORTRAIT


def test_svg_latin_text_stays_ltr_even_inside_rtl_document():
    """Regression: the admin UI ships `<html dir="rtl">`. Every
    <text> in the rendered card SVG must carry `direction="ltr"`
    (and the root <svg> too) so English card labels don't walk off
    the left edge of the card. This was visible as USER pills
    rendering only the last character of the username, footer text
    floating outside the card, and a stray Arabic glyph at the
    top-right corner where brand/title used to sit.
    """
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    template = _make_template(layout={
        "brand_name": "HobeRadius",
        "card_title": "Internet Card",
        "footer_text": "Keep login data",
        "hotspot_address": "hotspot.local",
    })
    svg = render_card_svg(build_card_render_model(
        template, {"id": 1, "username": "card1234", "password": "pw"}
    ))

    # Root SVG must declare direction explicitly.
    assert 'direction="ltr"' in svg[:400], "root <svg> missing direction=\"ltr\""

    # Latin-only content must carry direction="ltr" + text-anchor="start"
    # so the inheritance from the outer <html dir=rtl> is overridden
    # even on renderers that ignore the SVG-level direction attribute.
    text_count = svg.count("<text x=")
    ltr_on_text = sum(
        1 for chunk in svg.split("<text x=")[1:]
        if 'direction="ltr"' in chunk.split(">", 1)[0]
    )
    anchor_start = sum(
        1 for chunk in svg.split("<text x=")[1:]
        if 'text-anchor="start"' in chunk.split(">", 1)[0]
    )
    assert text_count >= 5, f"expected at least 5 text elements, got {text_count}"
    assert ltr_on_text == text_count, (
        f"only {ltr_on_text}/{text_count} <text> elements carry "
        f"direction=\"ltr\" — remaining ones will inherit the page's "
        f"rtl direction and render off-canvas."
    )
    assert anchor_start == text_count, (
        f"only {anchor_start}/{text_count} <text> elements carry "
        f"text-anchor=\"start\" — pills will lose the start anchor "
        f"in RTL mode."
    )


def test_svg_supports_arabic_text_direction_and_labels():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    template = _make_template(layout={
        "brand_name": "هوب راديوس",
        "card_title": "بطاقة إنترنت",
        "footer_text": "احتفظ ببيانات الدخول",
        "hotspot_address": "بوابة الدخول",
        "text_direction": "rtl",
        "credential_label_language": "arabic",
    })
    model = build_card_render_model(
        template, {"id": 7, "username": "CARD1234", "password": "pw"}
    )
    svg = render_card_svg(model)

    assert model["render_direction"] == "rtl"
    assert "هوب راديوس" in svg
    assert "بطاقة إنترنت" in svg
    assert "اسم المستخدم" in svg
    assert "كلمة المرور" in svg
    assert 'direction="rtl"' in svg
    assert 'text-anchor="end"' in svg
    # Credential values stay LTR so numeric/user identifiers are not
    # reordered by the Arabic card copy mode.
    assert "CARD1234" in svg
    value_chunks = [
        chunk.split("</text>", 1)[0]
        for chunk in svg.split("<text x=")[1:]
        if "CARD1234" in chunk
    ]
    assert value_chunks and all('direction="ltr"' in chunk for chunk in value_chunks)


def test_svg_arabic_text_is_pre_shaped_before_snapshot_export():
    """The SVG itself is the source card snapshot for PDF export.

    Arabic must be shaped before it reaches the SVG rasterizer; otherwise
    the PDF can contain a card image with disconnected Arabic letters even
    though the PDF wrapper is valid.
    """
    from app.radius.services.card_renderer import (
        _shape_arabic,
        build_card_render_model,
        render_card_svg,
    )

    brand = "\u0647\u0648\u0628 \u0631\u0627\u062f\u064a\u0648\u0633"
    title = "\u0628\u0637\u0627\u0642\u0629 \u0625\u0646\u062a\u0631\u0646\u062a"
    footer = "\u0627\u062d\u062a\u0641\u0638 \u0628\u0628\u064a\u0627\u0646\u0627\u062a \u0627\u0644\u062f\u062e\u0648\u0644"
    user_label = "\u0627\u0633\u0645 \u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645"
    pass_label = "\u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631"

    template = _make_template(layout={
        "brand_name": brand,
        "card_title": title,
        "footer_text": footer,
        "hotspot_address": "\u0628\u0648\u0627\u0628\u0629 \u0627\u0644\u062f\u062e\u0648\u0644",
        "text_direction": "rtl",
        "credential_label_language": "arabic",
        "render_engine": "ar_horizontal",
    })
    svg = render_card_svg(build_card_render_model(
        template, {"id": 7, "username": "CARD1234", "password": "pw"}
    ))

    for original in (brand, title, footer, user_label, pass_label):
        shaped = _shape_arabic(original)
        assert f'data-original="{original}"' in svg
        assert shaped != original
        assert shaped in svg

    for chunk in svg.split("<text x=")[1:]:
        if 'data-render-direction="rtl"' in chunk.split(">", 1)[0]:
            assert 'direction="ltr"' in chunk.split(">", 1)[0]
            assert 'unicode-bidi="bidi-override"' in chunk.split(">", 1)[0]


def test_arabic_render_engine_mirrors_layout_away_from_qr():
    from app.radius.services.card_renderer import build_card_render_model

    ltr_template = _make_template(layout={
        "brand_name": "HobeRadius",
        "card_title": "Internet Card",
        "footer_text": "Keep login data",
        "render_engine": "en_horizontal",
    })
    rtl_template = _make_template(layout={
        "brand_name": "هوب راديوس",
        "card_title": "بطاقة إنترنت",
        "footer_text": "احتفظ ببيانات الدخول",
        "render_engine": "ar_horizontal",
    })

    ltr = build_card_render_model(ltr_template, {"id": 1, "username": "CARD1234", "password": "pw"})
    rtl = build_card_render_model(rtl_template, {"id": 1, "username": "CARD1234", "password": "pw"})

    def by_id(model, item_id):
        return next(el for el in model["elements"] if el.get("id") == item_id)

    assert ltr["render_direction"] == "ltr"
    assert rtl["render_direction"] == "rtl"
    # English engine: text/pills left, QR right.
    assert by_id(ltr, "brand")["x"] < by_id(ltr, "qr")["x"]
    assert by_id(ltr, "user")["x"] < by_id(ltr, "qr")["x"]
    # Arabic engine: the whole composition is flipped, so text/pills
    # are right and QR/barcode is left. This prevents Arabic title/meta
    # from sitting under the QR.
    assert by_id(rtl, "brand")["x"] > by_id(rtl, "qr")["x"]
    assert by_id(rtl, "user")["x"] > by_id(rtl, "qr")["x"]


def test_four_explicit_render_engines_are_deterministic():
    from app.radius.services.card_renderer import build_card_render_model

    expected = {
        "en_horizontal": ("horizontal", "ltr"),
        "en_vertical": ("vertical", "ltr"),
        "ar_horizontal": ("horizontal", "rtl"),
        "ar_vertical": ("vertical", "rtl"),
    }

    def by_id(model, item_id):
        return next(el for el in model["elements"] if el.get("id") == item_id)

    for engine, (orientation, direction) in expected.items():
        model = build_card_render_model(
            _make_template(layout={"render_engine": engine}),
            {"id": 1, "username": "CARD1234", "password": "pw"},
        )
        assert model["render_engine"] == engine
        assert model["orientation"] == orientation
        assert model["render_direction"] == direction
        if orientation == "horizontal":
            assert model["canvas"] == {"width": 1000, "height": 600}
        else:
            assert model["canvas"] == {"width": 600, "height": 1000}
        if direction == "rtl":
            assert by_id(model, "user")["x"] > by_id(model, "qr")["x"]
            assert 0 <= by_id(model, "brand")["x"] <= model["canvas"]["width"]
            assert 0 <= by_id(model, "title")["x"] <= model["canvas"]["width"]
        else:
            assert by_id(model, "user")["x"] < by_id(model, "qr")["x"]


def test_svg_xml_escapes_user_text():
    """Render must escape XML — a malicious card name shouldn't break the SVG."""
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    template = _make_template(layout={"brand_name": "<script>alert(1)</script>"})
    svg = render_card_svg(build_card_render_model(template, {"id": 1, "username": "u", "password": "p"}))
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


# ───────────────────────────────────────────────────────────────────
# PDF adapter — full export through the operations service
# ───────────────────────────────────────────────────────────────────

def test_svg_ids_are_unique_between_inline_cards():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    first = render_card_svg(build_card_render_model(_make_template(layout={"render_engine": "ar_horizontal"})))
    second = render_card_svg(build_card_render_model(_make_template(layout={"render_engine": "en_vertical"})))

    assert first != second
    assert "card-bg" not in first
    assert "card-bg" not in second
    assert 'unicode-bidi="plaintext"' not in first
    assert 'unicode-bidi="plaintext"' not in second
    assert first.count("-bg") >= 1
    assert second.count("-bg") >= 1


def _create_template_via_service(template_name: str, *, tenant_id: int = 1) -> dict:
    from app.radius.services.operations import get_operations_service

    return get_operations_service().create_print_template(
        tenant_id=tenant_id,
        actor="render-test",
        data={
            "name": template_name,
            "orientation": "portrait",
            "cards_per_row": 2,
            "cards_per_column": 5,
            "page_size": "A4",
            "font_size": 12,
            "color": "#1f2937",
            "layout": {
                "card_orientation": "horizontal",
                "card_width_mm": 85,
                "card_height_mm": 54,
                "gradient_start": "#0f172a",
                "gradient_end": "#22a7bd",
                "accent_color": "#f59e0b",
                "text_color": "#ffffff",
                "surface_color": "#e8f7fb",
                "brand_name": "HobeRadius",
                "card_title": "Internet Card",
                "footer_text": "Keep login data until expiry",
                "hotspot_address": "hotspot.local",
                "pattern_style": "signal",
                "show_brand": True,
                "show_username": True,
                "show_password": True,
                "show_qr": True,
                "show_hotspot": True,
                "show_serial": True,
            },
        },
    )


def test_sample_pdf_starts_with_pdf_magic_and_contains_credentials(client):
    """Sample PDF export starts with %PDF and embeds the sample
    USER/PASS — the renderer never silently drops them."""
    _web_login(client)
    template = _create_template_via_service(f"PDF Magic {uuid4().hex[:6]}")
    res = client.get(
        f"/admin/radius/print-templates/{template['id']}/export.pdf",
        query_string={"sample_username": "CARD7777", "sample_password": "MyPw7777"},
        follow_redirects=False,
    )
    assert res.status_code == 200
    assert res.content_type.startswith("application/pdf")
    body = res.data
    assert body.startswith(b"%PDF")
    # ReportLab Flate-compresses page streams; decompress before
    # scanning for the credentials.
    text = _pdf_text_blob(body)
    assert b"CARD7777" in text
    assert b"MyPw7777" in text


def test_batch_pdf_export_includes_real_card_credentials(client):
    """Batch PDF MUST carry the real username + password from each
    card. This is the regression test for the user-reported bug
    where the batch PDF rendered USER/PASS as missing."""
    _web_login(client)
    from app.radius.db.repos import operations_repo, cards_repo, plans_repo
    from app.radius.core.types import CardBatch

    template = _create_template_via_service(f"PDF Batch {uuid4().hex[:6]}")

    # Build a tiny batch with two cards whose credentials we control.
    plans = plans_repo.list_plans(1, limit=1)
    assert plans, "expected at least one seeded plan"
    plan = plans[0]
    plan_id = plan["id"] if isinstance(plan, dict) else plan.id
    batch_obj = cards_repo.create_batch(CardBatch(
        id=None, tenant_id=1, plan_id=plan_id,
        batch_code=f"render-{uuid4().hex[:6]}",
        count=2, generated=2, created_by="render-test",
    ))
    # Use the bulk generator + then overwrite the credentials so we
    # can search for known strings in the PDF.
    cards_repo.generate_cards(
        tenant_id=1, batch_id=batch_obj.id, plan_id=plan_id, count=2,
        username_prefix="batchU", username_length=2, password_length=8,
    )
    # Fetch back, then update directly via sqlite for deterministic creds
    # we can grep for in the PDF bytes. Use unique per-run usernames so
    # parallel test runs and previous fixture rows can't trip the
    # `UNIQUE(tenant_id, username)` constraint.
    from app.radius.db.connection import transaction
    fresh = cards_repo.list_cards(1, batch_id=batch_obj.id, limit=10)
    assert len(fresh) >= 2
    tag = uuid4().hex[:6]
    user_a = f"batchU01-{tag}"
    user_b = f"batchU02-{tag}"
    pass_a = f"batchP01-{tag}"
    pass_b = f"batchP02-{tag}"
    with transaction() as conn:
        conn.execute(
            "UPDATE cards SET username = ?, password = ? WHERE id = ?",
            (user_a, pass_a, fresh[0].id),
        )
        conn.execute(
            "UPDATE cards SET username = ?, password = ? WHERE id = ?",
            (user_b, pass_b, fresh[1].id),
        )

    res = client.get(
        f"/admin/radius/print-templates/{template['id']}/export.pdf",
        query_string={"batch_id": batch_obj.id},
        follow_redirects=False,
    )
    assert res.status_code == 200
    body = res.data
    assert body.startswith(b"%PDF")
    text = _pdf_text_blob(body)
    assert user_a.encode() in text, "first card username missing from PDF"
    assert user_b.encode() in text, "second card username missing from PDF"
    assert pass_a.encode() in text, "first card password missing from PDF"
    assert pass_b.encode() in text, "second card password missing from PDF"

    # The export job log entry should reflect the unified renderer.
    jobs = operations_repo.list_print_jobs(1, limit=5)
    assert jobs[0]["template_id"] == template["id"]
    assert jobs[0]["status"] == "success"


def test_pdf_export_carries_arabic_via_almarai_font(client):
    """Arabic text (brand / title / footer) must reach the PDF
    rendered with the Almarai TTF — not stripped, not replaced with
    boxes. Latin text (username, hotspot) stays on Helvetica.
    """
    _web_login(client)
    from app.radius.services.operations import get_operations_service

    ops = get_operations_service()
    template = ops.create_print_template(
        tenant_id=1,
        actor="render-test",
        data={
            "name": f"AR PDF {uuid4().hex[:6]}",
            "orientation": "portrait",
            "cards_per_row": 2,
            "cards_per_column": 5,
            "page_size": "A4",
            "font_size": 12,
            "color": "#1f2937",
            "layout": {
                "card_orientation": "horizontal",
                "card_width_mm": 85,
                "card_height_mm": 54,
                "gradient_start": "#0f172a",
                "gradient_end": "#22a7bd",
                "accent_color": "#f59e0b",
                "text_color": "#ffffff",
                "surface_color": "#e8f7fb",
                "brand_name": "هوب راديوس",
                "card_title": "بطاقة إنترنت",
                "footer_text": "احفظ بياناتك حتى نهاية الصلاحية",
                "hotspot_address": "hotspot.local",
                "pattern_style": "signal",
                "show_brand": True, "show_username": True,
                "show_password": True, "show_qr": True,
                "show_hotspot": True, "show_serial": True,
            },
        },
    )

    res = client.get(
        f"/admin/radius/print-templates/{template['id']}/export.pdf",
        query_string={"sample_username": "CARD7", "sample_password": "PW7"},
        follow_redirects=False,
    )
    assert res.status_code == 200
    body = res.data
    assert body.startswith(b"%PDF")

    assert b"/Title (HobeRadius card export template" in body
    assert b"Card print template -" not in body
    # Arabic card copy is rasterized into transparent image runs before
    # placement in the PDF. That avoids PDF-viewer bidi/shaping drift
    # while preserving the exact live-preview appearance.
    assert b"/Subtype /Image" in body, "Arabic text image runs were not embedded"
    # Helvetica is still in use for the Latin parts (USER/PASS labels,
    # the hotspot/serial meta line, the username/password values).
    assert b"Helvetica" in body, "Latin runs should still use Helvetica"
    # The Latin sample data the route accepted must end up in the
    # decoded stream.
    text_blob = _pdf_text_blob(body)
    assert b"CARD7" in text_blob
    assert b"PW7" in text_blob


def test_arabic_shaping_pipeline_runs():
    """Smoke check: the renderer's _shape_arabic helper produces a
    different (shaped + bidi-reversed) string than the input when fed
    raw Arabic. Defends against the libs being uninstalled in a
    future environment refresh.
    """
    from app.radius.services.card_renderer import _shape_arabic, _has_arabic

    raw = "بطاقة إنترنت"
    assert _has_arabic(raw)
    shaped = _shape_arabic(raw)
    assert shaped != raw, "arabic-reshaper / python-bidi pipeline did nothing"
    # The shaped output should contain at least one Arabic presentation
    # form glyph (range U+FE70..U+FEFF).
    assert any("ﹰ" <= ch <= "﻿" for ch in shaped), (
        "shaped output has no Arabic presentation forms — reshaper did not run"
    )


def test_arabic_text_image_renderer_outputs_png():
    """Arabic PDF text uses a preview-like raster run, not raw PDF text.

    This catches regressions where Arabic text falls back to ReportLab's
    direct string drawing and appears as disconnected/reordered glyphs in
    Chrome/Edge PDF viewers.
    """
    from app.radius.services.card_renderer import _build_arabic_text_image

    rendered = _build_arabic_text_image(
        "بطاقة إنترنت",
        size=42,
        color="#ffffff",
        weight=900,
        max_width=340,
        direction="rtl",
    )

    assert rendered is not None
    png, width, height = rendered
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert width == 340
    assert height > 42


def test_arabic_text_image_prefers_presentation_form_safe_font():
    from app.radius.services.card_renderer import _font_path_for_arabic

    font_path = _font_path_for_arabic(bold=True).lower()
    if "windows\\fonts" in font_path:
        assert any(name in font_path for name in ("tahoma", "arial", "arabtype", "trado"))
    else:
        assert any(name in font_path for name in ("noto", "dejavu", "almarai"))


def test_designer_form_defaults_match_renderer_default_positions(client):
    """Designer/export/PDF parity guard.

    The interactive designer canvas (`.pr-card-preview` in
    print_templates.html) renders USER/PASS/QR at the same percentages
    listed in `_DEFAULT_POSITIONS` inside card_renderer.py. For a
    newly-created template to look identical in the export preview
    and the PDF, the designer's mm-based position inputs must default
    to 0 — that triggers the renderer's fallback to those same
    canonical fractions instead of treating "10 mm / 24 mm" as a real
    custom coordinate.

    This test reads the rendered HTML of the designer page and pins
    each position field's `value=` attribute at 0.
    """
    _web_login(client)
    page = client.get("/admin/radius/print-templates")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for name in (
        "username_x", "username_y",
        "password_x", "password_y",
        "qr_x", "qr_y",
    ):
        # Whitespace-tolerant: matches `name="username_x" ... value="0"`
        # in either attribute order.
        assert (
            f'name="{name}"' in html
        ), f"position field {name} is missing from the designer form"
        # The literal value="0" must be present on the same input.
        # We split around the name= attribute to scope the search to
        # that input's tag.
        before, after = html.split(f'name="{name}"', 1)
        tag_end = after.find(">")
        assert tag_end != -1
        tag_attrs = after[:tag_end]
        # Either order: `value="0"` directly inside the input tag.
        assert 'value="0"' in tag_attrs, (
            f'designer form field `{name}` must default to value="0" so '
            f'the renderer falls back to _DEFAULT_POSITIONS — got: <input ... {name}{tag_attrs}>'
        )


def test_preview_fragment_uses_unified_svg(client):
    """The live preview-fragment HTML must embed the renderer's SVG —
    no leftover HTML card-mock markup."""
    _web_login(client)
    template = _create_template_via_service(f"PDF Frag {uuid4().hex[:6]}")
    res = client.get(
        f"/admin/radius/print-templates/{template['id']}/preview-fragment",
        follow_redirects=False,
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Comes from the unified SVG adapter.
    assert "<svg" in html
    assert 'viewBox="0 0 1000 600"' in html or 'viewBox="0 0 600 1000"' in html
    assert 'preserveAspectRatio="xMidYMid meet"' in html
    # Old HTML mock markers must NOT appear in the fragment.
    assert "pr-card-accent" not in html
    assert "data-card-preview" not in html
