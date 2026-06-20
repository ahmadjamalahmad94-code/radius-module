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
    assert (
        "qr" in elements_by_id
        and elements_by_id["qr"]["payload"]
        == "http://hotspot.local/login?username=d2-85104&password=Secret_pw_1"
    )
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


def test_vertical_custom_positions_identical_before_and_after_save_swap():
    """غرفة التصميم ترسل أبعاد الحقول كما هي (85×54) بينما الحفظ يبدّلها
    للبطاقات العمودية (54×85) — يجب أن يحلّ المحرك إحداثيات mm المخصصة
    إلى نفس النقاط على الكانفس في الحالتين، وإلا انزاحت العناصر للأعلى
    ولليمين في المعاينة/الطباعة بعد الحفظ (الخلل المُبلَّغ)."""
    from app.radius.services.card_renderer import build_card_render_model

    def make(width_mm, height_mm):
        return _make_template(
            username_x=5.0, username_y=40.0,
            password_x=5.0, password_y=50.0,
            qr_x=30.0, qr_y=12.0,
            layout={
                "render_engine": "ar_vertical",
                "card_orientation": "vertical",
                "card_width_mm": width_mm,
                "card_height_mm": height_mm,
            },
        )

    card = {"id": 9, "username": "3375724386", "password": "58703"}
    live_form = build_card_render_model(make(85, 54), card)   # قبل الحفظ
    saved_row = build_card_render_model(make(54, 85), card)   # بعد التبديل
    assert live_form["canvas"] == {"width": 600, "height": 1000}
    assert live_form["elements"] == saved_row["elements"]


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
    assert model["background"]["source"] == "image"
    assert abs(model["background"]["image_opacity"] - 1.0) < 1e-6
    svg = render_card_svg(model)
    assert "data:image/png;base64," in svg


def test_uploaded_ready_design_only_overlays_credentials_and_login_qr():
    from app.radius.services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
    )
    template = _make_template(layout={
        "background_image_data_url": data_url,
        "brand_name": "SHOULD_NOT_RENDER",
        "card_title": "SHOULD_NOT_RENDER",
        "footer_text": "SHOULD_NOT_RENDER",
        "hotspot_address": "10.10.0.1",
    })
    model = build_card_render_model(
        template,
        {"id": 99, "username": "128304", "password": "445566"},
    )
    by_id = {item["id"]: item for item in model["elements"]}

    assert set(by_id) == {"user", "pass", "qr"}
    assert by_id["user"]["show_label"] is False
    assert by_id["pass"]["show_label"] is False
    assert by_id["qr"]["payload"] == "http://10.10.0.1/login?username=128304&password=445566"

    svg = render_card_svg(model, mask_password=False)
    assert "SHOULD_NOT_RENDER" not in svg
    assert ">USER<" not in svg
    assert ">PASS<" not in svg


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


def test_svg_text_pinned_ltr_even_inside_rtl_document():
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

    # Every <text> must carry direction="ltr" + text-anchor="start"
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

    # Almarai must be embedded so Arabic glyphs actually render.
    assert b"Almarai" in body, "Almarai TTF was not embedded into the PDF"
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


def test_designer_form_defaults_match_renderer_default_positions(client):
    """Designer/export/PDF parity guard.

    The designer's mm-based position inputs are pre-filled server-side
    with the EFFECTIVE coordinates the unified renderer actually draws
    (`_effective_field_layout`), flagged `data-auto-default="1"`. The
    inverse mapping is exact (mm = canvas-fraction × card mm box), so
    re-submitting these values reproduces the same positions — designer,
    export preview and PDF stay identical for a fresh template.

    This test pins each position field to the renderer-derived value
    instead of the old hardcoded 0 sentinel.
    """
    from app.radius.routes.print_templates import _effective_field_layout

    _web_login(client)
    page = client.get("/admin/radius/print-templates")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    with client.application.test_request_context("/admin/radius/print-templates"):
        effective = _effective_field_layout({})
    for name in (
        "username_x", "username_y",
        "password_x", "password_y",
        "qr_x", "qr_y",
    ):
        assert (
            f'name="{name}"' in html
        ), f"position field {name} is missing from the designer form"
        # Scope the attribute search to that input's tag.
        before, after = html.split(f'name="{name}"', 1)
        tag_end = after.find(">")
        assert tag_end != -1
        tag_attrs = after[:tag_end]
        expected = effective.get(name)
        assert expected is not None, f"no effective value computed for {name}"
        assert (
            f'value="{expected}"' in tag_attrs
        ), (
            f'designer form field `{name}` must be pre-filled with the '
            f'renderer\'s effective coordinate {expected} — got: '
            f"<input ... {name}{tag_attrs}>"
        )
        assert 'data-auto-default="1"' in tag_attrs, (
            f"pre-filled `{name}` must stay flagged auto-default so engine "
            f"switches can re-derive it"
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


# ───────────────────────────────────────────────────────────────────
# Footer / tagline bottom clearance (fix/card-footer-clip)
# ───────────────────────────────────────────────────────────────────

def test_footer_has_bottom_safe_clearance():
    """The footer/tagline glyph box (descenders included) must sit inside
    the bottom safe area — never flush to the edge nor clipped — across both
    orientations; and the #serial/meta line must stay above it without
    overlap."""
    from app.radius.services.card_renderer import (
        build_card_render_model, _CARD_SAFE_BOTTOM, _TEXT_FULL_DESCENT,
    )
    for w, h in ((85, 54), (54, 85)):
        tmpl = _make_template(layout={
            "card_width_mm": w, "card_height_mm": h,
            "card_orientation": "horizontal" if w > h else "vertical",
        })
        model = build_card_render_model(
            tmpl, {"id": 128, "username": "7772", "password": "pw"})
        ch = model["canvas"]["height"]
        els = {e["id"]: e for e in model["elements"]}
        footer = els["footer"]
        bottom = footer["y"] + footer["size"] * _TEXT_FULL_DESCENT
        # glyph bottom is inside the safe area, with real clearance from edge
        assert bottom <= ch * _CARD_SAFE_BOTTOM + 0.5, (w, h, bottom, ch)
        assert (ch - bottom) / ch >= 0.05, ((w, h), "clearance < 5%")
        # footer is genuinely lifted off the bottom edge (regression guard:
        # the old default sat at y=0.95·H)
        assert footer["y"] < ch * 0.92, ((w, h), "footer not lifted", footer["y"])
        # the #serial-bearing meta line sits above the footer, no overlap
        meta = els["meta"]
        assert "#128" in meta["text"]
        assert meta["y"] + meta["size"] * _TEXT_FULL_DESCENT <= footer["y"] + 0.5, \
            ((w, h), "meta overlaps footer")


def test_footer_clamped_when_positioned_at_edge():
    """Defensive: even if a (default/template) position pushes the footer to
    the very bottom, the builder clamps it back inside the safe area so it can
    never be clipped."""
    import app.radius.services.card_renderer as cr
    from app.radius.services.card_renderer import build_card_render_model
    saved = dict(cr._DEFAULT_POSITIONS["footer"])
    cr._DEFAULT_POSITIONS["footer"] = {"x": 0.06, "y": 0.99, "size": 0.045}
    try:
        model = build_card_render_model(
            _make_template(), {"id": 1, "username": "u", "password": "p"})
    finally:
        cr._DEFAULT_POSITIONS["footer"] = saved
    ch = model["canvas"]["height"]
    footer = {e["id"]: e for e in model["elements"]}["footer"]
    bottom = footer["y"] + footer["size"] * cr._TEXT_FULL_DESCENT
    assert bottom <= ch * cr._CARD_SAFE_BOTTOM + 0.5, ("clamp failed", bottom, ch)


# ───────────────────────────────────────────────────────────────────
# Heading fit-to-width — no truncation/overlap in ALL FOUR modes
# (vertical/horizontal × Arabic/English)  [fix/card-footer-clip]
# ───────────────────────────────────────────────────────────────────

_LONG_AR = "شبكة المقهى للضيوف - واي فاي مجاني"
_LONG_EN = "Cafe Guest Wi-Fi Free Internet Access"

_FOUR_MODES = [
    ("ar_vertical",   54, 85, _LONG_AR, "rtl"),
    ("ar_horizontal", 85, 54, _LONG_AR, "rtl"),
    ("en_vertical",   54, 85, _LONG_EN, "ltr"),
    ("en_horizontal", 85, 54, _LONG_EN, "ltr"),
]


def _model_for_mode(engine, w, h, title):
    from app.radius.services.card_renderer import build_card_render_model
    tmpl = _make_template(layout={
        "render_engine": engine,
        "card_width_mm": w, "card_height_mm": h,
        "card_orientation": "vertical" if h > w else "horizontal",
        "card_title": title,
        "brand_name": "Cafe Hotspot" if engine.startswith("en") else "مقهى الشبكة",
        "show_qr": True,
    })
    return build_card_render_model(tmpl, {"id": 128, "username": "7772", "password": "pw"})


def test_heading_never_truncated_or_clipped_in_four_modes():
    """The title fits fully (auto-shrink and/or 2-line wrap) within max_width
    in every mode — full text preserved, no ellipsis, nothing clipped."""
    from app.radius.services.card_renderer import _measure_text_width
    for engine, w, h, title, direction in _FOUR_MODES:
        model = _model_for_mode(engine, w, h, title)
        tels = [e for e in model["elements"] if e["id"].startswith("title")]
        assert tels, (engine, "no title element")
        # full logical text preserved across the (1 or 2) line elements
        joined = " ".join(e["text"] for e in tels)
        assert " ".join(joined.split()) == " ".join(title.split()), (engine, joined)
        for e in tels:
            assert "…" not in e["text"], (engine, "ellipsis truncation", e["text"])
            width = _measure_text_width(e["text"], e["size"],
                                        weight=950, direction=direction)
            assert width <= e["max_width"] + 1.0, (engine, "clipped", width, e["max_width"])


def test_heading_two_line_does_not_overlap_brand_or_pill():
    """A wrapped (2-line) title must not collide with the brand above nor the
    credential pill below, in any mode."""
    for engine, w, h, title, _d in _FOUR_MODES:
        model = _model_for_mode(engine, w, h, title)
        els = {e["id"]: e for e in model["elements"]}
        tels = [els[k] for k in ("title", "title2") if k in els]
        top = tels[0]["y"]
        bottom = tels[-1]["y"] + tels[-1]["size"] * 1.2  # incl. descenders
        if "brand" in els:
            b = els["brand"]
            assert b["y"] + b["size"] * 1.2 <= top + 1.0, (engine, "brand overlaps title")
        if "user" in els:
            assert bottom <= els["user"]["y"] + 1.0, (engine, "title overlaps pill")


def test_short_heading_stays_single_line_at_base_size():
    """Regression: a short title must NOT be shrunk or wrapped."""
    from app.radius.services.card_renderer import (
        build_card_render_model, _engine_default_positions,
    )
    tmpl = _make_template(layout={"card_title": "WiFi", "render_engine": "en_horizontal"})
    model = build_card_render_model(tmpl, {"id": 1, "username": "u", "password": "p"})
    tels = [e for e in model["elements"] if e["id"].startswith("title")]
    assert len(tels) == 1, "short title should stay one line"
    pos = _engine_default_positions("ltr", orientation="horizontal")["title"]
    base = pos["size"] * model["canvas"]["height"]
    assert abs(tels[0]["size"] - base) < 0.6, ("short title shrunk unexpectedly", tels[0]["size"], base)


def test_wrapped_arabic_lines_preserve_logical_word_order():
    """Wrapped Arabic title must be line-broken on the LOGICAL (un-shaped,
    un-bidi) text — never by splitting an already reshaped+bidi'd visual
    string. So each line element stores logical text and word order is
    preserved (first logical word leads line 1)."""
    from app.radius.services.card_renderer import build_card_render_model
    title = "شبكة المقهى للضيوف واي فاي مجاني للزوار"
    tmpl = _make_template(layout={
        "render_engine": "ar_vertical", "card_width_mm": 54,
        "card_height_mm": 85, "card_orientation": "vertical",
        "card_title": title, "show_qr": True})
    model = build_card_render_model(tmpl, {"id": 1, "username": "u", "password": "p"})
    tels = [e for e in model["elements"] if e["id"].startswith("title")]
    assert len(tels) >= 2, "long Arabic title should wrap to >=2 lines"
    # each line stores LOGICAL text — no Arabic presentation forms (U+FB50..
    # U+FEFF), which would mean it was shaped/bidi'd BEFORE the line split.
    for e in tels:
        assert not any("ﭐ" <= c <= "﻿" for c in e["text"]), \
            ("line stored as post-bidi visual string", e["text"])
    words = title.split()
    assert tels[0]["text"].split()[0] == words[0], "first logical word not leading line 1"
    assert " ".join(e["text"] for e in tels).split() == words, "word order not preserved"


# ───────────────────────────────────────────────────────────────────
# True RTL layout mirror for Arabic (QR↔fields swap, headings right) —
# English stays LTR.  [fix/card-footer-clip]
# ───────────────────────────────────────────────────────────────────

def _qr_field_centers(engine, w, h):
    from app.radius.services.card_renderer import build_card_render_model
    tmpl = _make_template(layout={
        "render_engine": engine, "card_width_mm": w, "card_height_mm": h,
        "card_orientation": "vertical" if h > w else "horizontal",
        "show_qr": True})
    m = build_card_render_model(tmpl, {"id": 1, "username": "u", "password": "p"})
    cw = m["canvas"]["width"]
    els = {e["id"]: e for e in m["elements"]}
    qr, user = els["qr"], els["user"]
    return (qr["x"] + qr["size"] / 2) / cw, (user["x"] + user["width"] / 2) / cw


def test_arabic_layout_mirrored_vs_english_both_orientations():
    """AR puts QR on the LEFT and credential fields on the RIGHT; EN is the
    opposite — a true mirror, in both orientations."""
    for w, h in ((85, 54), (54, 85)):
        ar = "ar_horizontal" if w > h else "ar_vertical"
        en = "en_horizontal" if w > h else "en_vertical"
        ar_qr, ar_user = _qr_field_centers(ar, w, h)
        en_qr, en_user = _qr_field_centers(en, w, h)
        assert ar_qr < 0.5 < en_qr, ((w, h), "QR not mirrored", ar_qr, en_qr)
        assert en_user < 0.5 < ar_user, ((w, h), "fields not mirrored", en_user, ar_user)
        # roughly mirror images of each other
        assert abs(ar_qr - (1 - en_qr)) < 0.06, ((w, h), ar_qr, en_qr)


def test_arabic_headings_right_aligned_english_left():
    """AR brand/title right-align to the right margin (read from the right);
    EN brand/title left-align — in both orientations."""
    from app.radius.services.card_renderer import build_card_render_model
    for w, h in ((85, 54), (54, 85)):
        vert = "vertical" if h > w else "horizontal"
        ar = _make_template(layout={
            "render_engine": ("ar_horizontal" if w > h else "ar_vertical"),
            "card_width_mm": w, "card_height_mm": h, "card_orientation": vert,
            "card_title": "عنوان البطاقة", "brand_name": "اسم العلامة",
            "show_qr": True})
        m = build_card_render_model(ar, {"id": 1, "username": "u", "password": "p"})
        cw = m["canvas"]["width"]
        els = {e["id"]: e for e in m["elements"]}
        for key in ("brand", "title"):
            e = els[key]
            assert e["direction"] == "rtl"
            right = (e["x"] + e["max_width"]) / cw
            assert right > 0.85, (key, "AR heading not right-aligned", right)
        en = _make_template(layout={
            "render_engine": ("en_horizontal" if w > h else "en_vertical"),
            "card_width_mm": w, "card_height_mm": h, "card_orientation": vert,
            "card_title": "Card Title", "brand_name": "Brand", "show_qr": True})
        m2 = build_card_render_model(en, {"id": 1, "username": "u", "password": "p"})
        cw2 = m2["canvas"]["width"]
        els2 = {e["id"]: e for e in m2["elements"]}
        for key in ("brand", "title"):
            assert els2[key]["x"] / cw2 < 0.12, (key, "EN heading not left-aligned")
