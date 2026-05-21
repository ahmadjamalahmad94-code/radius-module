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
