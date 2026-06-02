"""Web UI smoke tests for card print templates."""
from __future__ import annotations

import base64
import time
from io import BytesIO
from uuid import uuid4

import pytest


def _image_upload_bytes(mode: str = "RGBA", size: tuple[int, int] = (1, 1), fmt: str = "PNG") -> bytes:
    from PIL import Image

    color = (24, 167, 189, 180) if mode == "RGBA" else (24, 167, 189)
    img = Image.new(mode, size, color)
    out = BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


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


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"print_web_{uuid4().hex[:10]}"
    password = "print-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Print Template Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_print_templates_web_route_is_login_guarded(client):
    res = client.get("/admin/radius/print-templates", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_print_templates_create_and_visual_preview(client):
    _web_login(client)
    page = client.get("/admin/radius/print-templates")
    assert page.status_code == 200
    page_html = page.get_data(as_text=True)
    # Designer canvas + form structure (post-SVG-rewrite).
    assert "معاينة البطاقة" in page_html or "معاينة حية" in page_html
    assert 'name="render_engine"' in page_html
    assert 'name="card_orientation"' in page_html
    assert 'name="text_direction"' in page_html
    assert 'name="credential_label_language"' in page_html
    assert "pr-preset-picker" in page_html
    assert "pr-preset-select-field" in page_html
    assert "pr-design-source" in page_html
    assert 'data-design-source-option="image"' in page_html
    assert 'data-design-source-option="preset"' in page_html
    assert 'data-design-source="' in page_html
    assert 'data-upload-design-only' in page_html
    assert 'data-system-design-only' in page_html
    assert 'name="qr_size_pct"' in page_html
    assert 'name="qr_color"' in page_html
    assert 'name="credential_text_color"' in page_html
    assert 'name="username_surface_enabled"' in page_html
    assert 'name="show_username" value="0"' in page_html
    assert "function setDesignSource" in page_html
    assert "pr-engine-options" in page_html
    assert 'data-engine-option="ar_horizontal"' in page_html
    assert 'data-engine-option="ar_vertical"' in page_html
    assert 'data-engine-option="en_horizontal"' in page_html
    assert 'data-engine-option="en_vertical"' in page_html
    assert page_html.index("pr-engine-card") < page_html.index("pr-preset-picker")
    assert "function setEngineSelection" in page_html
    assert "[data-engine-option]" in page_html
    assert "????" not in page_html
    assert "اختر شكل القالب" in page_html
    assert "اختيار بصري سريع" in page_html
    assert "قالب حديث" in page_html
    assert page_html.index("pr-preset-picker") < page_html.index('name="name"')
    assert "localizedDefaults" in page_html
    assert "بطاقة إنترنت" in page_html
    assert "احتفظ ببيانات الدخول حتى انتهاء الصلاحية" in page_html
    assert "استخدم اسم المستخدم وكلمة المرور أو رمز QR لتسجيل الدخول." in page_html
    assert "applyLanguageDefaults(profile.credential_label_language === 'arabic'" in page_html
    assert ".pr-svg-mount.is-vertical" in page_html
    assert "position:absolute;inset:22px" in page_html
    assert "height:min(360px, 100%)" in page_html
    assert "function syncPreviewOrientation()" in page_html
    assert "svgMount.dataset.renderEngine = engine" in page_html
    assert "openDesignerDrawerFromHash()" in page_html
    assert "setTimeout(() => fetchSvg(seq), 120)" in page_html
    assert "if(seq !== fetchSeq) return;" in page_html
    assert 'name="background_image"' in page_html
    assert 'name="background_style"' in page_html
    assert "رسوم جاهزة من النظام" in page_html
    assert "صورة محفوظة / مرفوعة" in page_html
    assert 'name="background_image_name"' in page_html
    # Legacy HTML drag handles (data-drag="username") were deleted
    # when the designer preview moved to the unified SVG renderer.
    # The new mount + endpoint take their place:
    assert "data-designer-svg-mount" in page_html
    assert "/print-templates/designer-svg" in page_html
    assert 'name="show_title"' in page_html
    assert 'data-layer-section="username"' in page_html
    assert 'data-layer-section="password"' in page_html
    assert 'data-layer-section="barcode"' in page_html
    assert 'type="range" name="username_font_size"' in page_html
    assert 'type="range" name="password_font_size"' in page_html
    assert 'type="range" name="qr_size_pct" min="0"' in page_html
    # Commit 3 collapsed `/print-templates/export` into the `#export`
    # anchor on the same page; the legacy URL still lives on the
    # per-row "فتح التصدير" buttons, but those only render when the
    # tenant has saved templates. Either entry point counts — the
    # assertion's intent is "there is a way to reach the export
    # workflow from this page".
    assert (
        "/admin/radius/print-templates/export" in page_html
        or "#export" in page_html
    )
    assert "data-export-gallery" not in page_html

    hidden_preview = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={
            "card_orientation": "horizontal",
            "background_style": "preset",
            "card_title": "TITLE_HIDE",
            "show_brand": "0",
            "show_title": "0",
            "show_username": "0",
            "show_password": "0",
            "show_qr": "0",
            "show_hotspot": "0",
            "show_validity": "0",
            "show_serial": "0",
            "show_price": "0",
            "sample_username": "SHOULD_HIDE",
        },
    )
    assert hidden_preview.status_code == 200
    hidden_svg = hidden_preview.get_data(as_text=True)
    assert "TITLE_HIDE" not in hidden_svg
    assert "SHOULD_HIDE" not in hidden_svg
    assert "card-qr" not in hidden_svg

    token = _csrf(client, "/admin/radius/print-templates")
    name = f"Print UI {uuid4().hex[:8]}"
    created = client.post(
        "/admin/radius/print-templates",
        data={
            "_csrf_token": token,
            "name": name,
            "orientation": "landscape",
            "cards_per_row": "3",
            "cards_per_column": "4",
            "page_size": "A4",
            "render_engine": "ar_vertical",
            "show_qr": "1",
            "username_x": "10",
            "username_y": "15",
            "password_x": "10",
            "password_y": "25",
            "qr_x": "60",
            "qr_y": "12",
            "font_size": "11",
            "color": "#1f2937",
            "card_width_mm": "85",
            "card_height_mm": "54",
            "card_orientation": "vertical",
            "pattern_style": "grid",
            "image_opacity": "0.7",
            "background_image": (BytesIO(_image_upload_bytes()), "card-bg.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert created.status_code == 200
    created_html = created.get_data(as_text=True)
    assert name in created_html
    assert "فتح التصدير" in created_html

    # Commit 3: the legacy /export URL now redirects to the merged
    # /print-templates#export anchor. Make sure the redirect lands on
    # the canonical page and that page contains the export-room markup.
    export_redirect = client.get("/admin/radius/print-templates/export")
    assert export_redirect.status_code == 302
    assert export_redirect.headers["Location"].endswith(
        "/admin/radius/print-templates#export"
    )
    export_center = client.get(
        "/admin/radius/print-templates/export", follow_redirects=True
    )
    assert export_center.status_code == 200
    export_html = export_center.get_data(as_text=True)
    assert "data-export-room" in export_html
    assert "data-export-template-card" in export_html
    assert "data-open-template-picker" in export_html
    assert "data-template-modal" in export_html
    assert "data-selected-template-summary" in export_html
    assert "data-export-job-url-template" in export_html
    assert "pr-chip-list-panel" not in export_html
    assert "data-export-progress" in export_html
    assert "data-export-print-setting" in export_html
    assert 'name="print_columns"' in export_html
    assert 'name="print_rows"' in export_html
    assert 'name="print_column_gap_mm"' in export_html
    assert 'name="print_row_gap_mm"' in export_html
    assert 'name="print_margin_top_mm"' in export_html
    assert 'name="cards_per_row"' not in export_html
    assert 'name="cards_per_column"' not in export_html
    assert "data-preview-mount" in export_html  # live preview-fragment mount
    assert "pr-designer-drawer" in export_html  # designer collapsed into drawer
    assert name in export_html

    from app.radius.db.repos import operations_repo

    templates = operations_repo.list_print_templates(1, limit=1000)
    template = next(item for item in templates if item["name"] == name)
    layout = template["layout_json"]
    assert layout["render_engine"] == "ar_vertical"
    assert layout["card_orientation"] == "vertical"
    assert layout["pattern_style"] == "grid"
    assert layout["background_image_name"] == "card-bg.png"
    assert layout["background_image_data_url"].startswith("data:image/png;base64,")
    assert layout["background_image_optimized"] is True
    assert layout["background_image_original_bytes"] > 0
    assert layout["background_image_optimized_bytes"] > 0

    edit_page = client.get(
        f"/admin/radius/print-templates?edit_template={template['id']}#designer"
    )
    assert edit_page.status_code == 200
    edit_html = edit_page.get_data(as_text=True)
    assert f"/admin/radius/print-templates/{template['id']}/edit" in edit_html

    updated_name = f"{name} Updated"
    updated = client.post(
        f"/admin/radius/print-templates/{template['id']}/edit",
        data={
            "_csrf_token": token,
            "name": updated_name,
            "render_engine": "ar_horizontal",
            "card_width_mm": "85",
            "card_height_mm": "54",
            "background_style": "preset",
            "show_title": "0",
            "show_username": "1",
            "show_password": "1",
            "show_qr": "1",
            "username_x": "12",
            "username_y": "16",
            "password_x": "12",
            "password_y": "28",
            "qr_x": "48",
            "qr_y": "12",
            "qr_size_pct": "0",
            "font_size": "11",
            "color": "#1f2937",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    refreshed = operations_repo.get_print_template(1, template["id"])
    assert refreshed is not None
    assert refreshed["name"] == updated_name
    assert refreshed["layout_json"]["show_title"] is False
    assert refreshed["layout_json"]["qr_size_pct"] == 0

    preview = client.post(
        f"/admin/radius/print-templates/{template['id']}/preview",
        data={"_csrf_token": token, "sample_username": "QA123"},
        follow_redirects=True,
    )
    assert preview.status_code == 200
    html = preview.get_data(as_text=True)
    # Post-SVG-rewrite: the designer canvas no longer carries the
    # legacy `pt-visual-card` / `visual_card_preview` HTML wrappers.
    # The unified SVG renderer drives the canvas, so we assert the
    # new mount + the per-card sample username.
    assert "data-designer-svg-mount" in html
    assert "QA123" in html
    assert "data-print-preview-summary" in html
    assert "تفاصيل المعاينة" in html
    assert "محرك المعاينة" in html
    assert "{{ preview.preview | tojson" not in html
    assert "{&#34;renderer&#34;" not in html

    export = client.get(
        f"/admin/radius/print-templates/{template['id']}/export.pdf",
        query_string={"price_text": "JOD 9", "hotspot_address": "demo.hotspot"},
        follow_redirects=False,
    )
    assert export.status_code == 200
    assert export.content_type.startswith("application/pdf")
    assert export.data.startswith(b"%PDF")


def test_print_template_background_upload_is_optimized_before_save(client):
    from PIL import Image

    _web_login(client)
    token = _csrf(client, "/admin/radius/print-templates")
    img = Image.new("RGB", (2400, 1600))
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            pixels[x, y] = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
    raw = BytesIO()
    img.save(raw, format="JPEG", quality=100, subsampling=0)
    original = raw.getvalue()

    name = f"Compressed BG {uuid4().hex[:8]}"
    created = client.post(
        "/admin/radius/print-templates",
        data={
            "_csrf_token": token,
            "name": name,
            "render_engine": "ar_horizontal",
            "design_preset": "modern",
            "background_image": (BytesIO(original), "customer-bg.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert created.status_code == 200

    from app.radius.db.repos import operations_repo

    template = next(
        item for item in operations_repo.list_print_templates(1, limit=1000)
        if item["name"] == name
    )
    layout = template["layout_json"]
    assert layout["background_image_name"] == "customer-bg.jpg"
    assert layout["background_image_mime"] == "image/jpeg"
    assert layout["background_image_original_mime"] == "image/jpeg"
    assert layout["background_image_original_bytes"] == len(original)
    assert layout["background_image_optimized_bytes"] < len(original)
    assert max(layout["background_image_width"], layout["background_image_height"]) <= 1400
    assert layout["background_image_quality"] in {84, 78, 72}
    assert layout["background_image_data_url"].startswith("data:image/jpeg;base64,")


def test_print_template_background_data_url_is_saved_and_exportable(client):
    _web_login(client)
    token = _csrf(client, "/admin/radius/print-templates")
    data_url = "data:image/png;base64," + base64.b64encode(
        _image_upload_bytes(mode="RGB", size=(24, 18), fmt="PNG")
    ).decode("ascii")
    name = f"Hidden BG {uuid4().hex[:8]}"

    created = client.post(
        "/admin/radius/print-templates",
        data={
            "_csrf_token": token,
            "name": name,
            "render_engine": "ar_horizontal",
            "design_preset": "modern",
            "background_image_data_url": data_url,
            "background_image_name": "preview-only.png",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200

    from app.radius.db.repos import operations_repo

    template = next(
        item for item in operations_repo.list_print_templates(1, limit=1000)
        if item["name"] == name
    )
    layout = template["layout_json"]
    assert layout["background_style"] == "image"
    assert layout["background_image_name"] == "preview-only.png"
    assert layout["background_image_data_url"].startswith("data:image/")
    assert layout["background_image_optimized"] is True

    svg = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={
            "render_engine": "ar_horizontal",
            "background_image_data_url": layout["background_image_data_url"],
            "sample_username": "CARD1234",
        },
    )
    assert svg.status_code == 200
    assert "data:image/" in svg.get_data(as_text=True)

    export = client.get(f"/admin/radius/print-templates/{template['id']}/export.pdf")
    assert export.status_code == 200
    assert export.data.startswith(b"%PDF")
    assert b"/Subtype /Image" in export.data
    assert b"/Width 24" in export.data
    assert b"/Height 18" in export.data


def test_print_template_background_modes_are_separate(client):
    _web_login(client)
    data_url = "data:image/png;base64," + base64.b64encode(
        _image_upload_bytes(mode="RGB", size=(12, 12), fmt="PNG")
    ).decode("ascii")

    preset_svg = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={
            "render_engine": "ar_horizontal",
            "background_style": "preset",
            "background_image_data_url": data_url,
            "pattern_style": "signal",
            "sample_username": "CARD1234",
        },
    )
    assert preset_svg.status_code == 200
    preset_html = preset_svg.get_data(as_text=True)
    assert "<image" not in preset_html
    assert "patternUnits" in preset_html

    image_svg = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={
            "render_engine": "ar_horizontal",
            "background_style": "image",
            "background_image_data_url": data_url,
            "pattern_style": "signal",
            "sample_username": "CARD1234",
        },
    )
    assert image_svg.status_code == 200
    image_html = image_svg.get_data(as_text=True)
    assert "<image" in image_html
    assert data_url in image_html
    assert "patternUnits" not in image_html

    legacy_gradient_svg = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={
            "render_engine": "ar_horizontal",
            "background_style": "gradient",
            "background_image_data_url": data_url,
            "pattern_style": "signal",
            "sample_username": "CARD1234",
        },
    )
    assert legacy_gradient_svg.status_code == 200
    legacy_html = legacy_gradient_svg.get_data(as_text=True)
    assert "<image" in legacy_html
    assert data_url in legacy_html


def test_print_templates_designer_svg_endpoint_is_uncached_svg(client):
    _web_login(client)

    res = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={
            "brand_name": "HobeRadius",
            "card_title": "Internet Card",
            "sample_username": "CARD1234",
            "card_width_mm": "85",
            "card_height_mm": "54",
            "card_orientation": "horizontal",
            "show_qr": "1",
        },
    )

    assert res.status_code == 200
    assert res.content_type.startswith("image/svg+xml")
    assert "no-store" in res.headers.get("Cache-Control", "")
    html = res.get_data(as_text=True)
    assert "<svg" in html
    assert "CARD1234" in html


def test_print_templates_async_export_job_can_be_polled_and_downloaded(client):
    _web_login(client)
    token = _csrf(client, "/admin/radius/print-templates")
    name = "Async Export " + uuid4().hex[:8]
    created = client.post(
        "/admin/radius/print-templates",
        data={
            "_csrf_token": token,
            "name": name,
            "render_engine": "en_horizontal",
            "design_preset": "modern",
            "brand_name": "HobeRadius",
            "card_title": "Internet Card",
            "show_qr": "1",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    from app.radius.db.repos import operations_repo

    template = next(
        item for item in operations_repo.list_print_templates(1, limit=1000)
        if item["name"] == name
    )
    started = client.post(
        f"/admin/radius/print-templates/{template['id']}/export-jobs",
        data={
            "_csrf_token": token,
            "sample_username": "ASYNC123",
            "sample_password": "SECRET",
        },
    )
    assert started.status_code == 202
    payload = started.get_json()
    assert payload["ok"] is True
    assert payload["job"]["status"] == "queued"
    status_url = payload["status_url"]
    download_url = payload["download_url"]

    job = None
    for _ in range(80):
        polled = client.get(status_url)
        assert polled.status_code == 200
        job = polled.get_json()["job"]
        if job["status"] == "success":
            break
        time.sleep(0.05)
    assert job is not None
    assert job["status"] == "success"
    assert job["download_ready"] is True
    assert job["progress"] == 100
    pdf = client.get(download_url)
    assert pdf.status_code == 200
    assert pdf.content_type.startswith("application/pdf")
    assert pdf.data.startswith(b"%PDF")


def test_print_templates_designer_script_has_resilient_svg_refresh(client):
    _web_login(client)

    res = client.get("/admin/radius/print-templates")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "function showSvgPlaceholder" in html
    assert "cache: 'no-store'" in html
    assert "function update()" in html
    assert "تعذّر تحديث المعاينة" in html
    assert "if(!picker) return;" in html


def test_print_template_validation_error_preserves_designer_form(client):
    _web_login(client)

    token = _csrf(client, "/admin/radius/print-templates")
    name = f"Duplicate Guard {uuid4().hex[:8]}"
    first = client.post(
        "/admin/radius/print-templates",
        data={
            "_csrf_token": token,
            "name": name,
            "orientation": "portrait",
            "page_size": "A4",
            "cards_per_row": "2",
            "cards_per_column": "5",
            "design_preset": "modern",
            "font_size": "12",
            "color": "#ffffff",
        },
        follow_redirects=True,
    )
    assert first.status_code == 200

    token = _csrf(client, "/admin/radius/print-templates")
    duplicate = client.post(
        "/admin/radius/print-templates",
        data={
            "_csrf_token": token,
            "name": name,
            "orientation": "landscape",
            "page_size": "Letter",
            "cards_per_row": "4",
            "cards_per_column": "3",
            "render_engine": "ar_vertical",
            "card_width_mm": "90",
            "card_height_mm": "58",
            "font_size": "17",
            "card_orientation": "vertical",
            "design_preset": "gold",
            "pattern_style": "grid",
            "text_direction": "rtl",
            "credential_label_language": "arabic",
            "gradient_start": "#111827",
            "gradient_end": "#f59e0b",
            "accent_color": "#22c55e",
            "text_color": "#f8fafc",
            "surface_color": "#ecfeff",
            "brand_name": "شبكة الاختبار",
            "card_title": "كرت سريع",
            "hotspot_address": "login.example",
            "price_text": "JOD 2",
            "validity_text": "6 ساعات",
            "footer_text": "احتفظ بالبيانات",
            "instructions_text": "تعليمات خاصة",
            "username_x": "12.5",
            "username_y": "21.5",
            "password_x": "12.5",
            "password_y": "33.5",
            "qr_x": "61.5",
            "qr_y": "14.5",
            "show_brand": "1",
            "show_username": "1",
            "show_password": "1",
            "show_qr": "1",
            "show_hotspot": "1",
            "show_validity": "1",
            "show_serial": "1",
        },
        follow_redirects=False,
    )

    assert duplicate.status_code == 400
    html = duplicate.get_data(as_text=True)
    assert "pr-designer-drawer\" id=\"designer\" open" in html
    assert "شبكة الاختبار" in html
    assert "كرت سريع" in html
    assert "login.example" in html
    assert "JOD 2" in html
    assert 'value="4"' in html
    assert 'value="17"' in html
    assert 'value="12.5"' in html
    assert 'value="61.5"' in html
    assert 'value="vertical" selected' in html
    assert 'value="ar_vertical" selected' in html
    assert 'value="gold" selected' in html
    assert 'value="rtl" selected' in html
    assert 'value="arabic" selected' in html


def test_print_templates_set_default_marks_unique_template(client):
    """Commit 4: starring a template marks it default, clears any prior
    default, persists in layout_json, and shows the افتراضي badge."""
    _web_login(client)
    from app.radius.db.repos import operations_repo
    from app.radius.services.operations import get_operations_service

    ops = get_operations_service()
    # Two templates so we can assert the previous default is cleared
    # when a new one is starred.
    name_a = f"Default A {uuid4().hex[:8]}"
    name_b = f"Default B {uuid4().hex[:8]}"
    for nm in (name_a, name_b):
        token = _csrf(client, "/admin/radius/print-templates")
        client.post(
            "/admin/radius/print-templates",
            data={
                "_csrf_token": token,
                "name": nm,
                "orientation": "portrait",
                "page_size": "A4",
                "cards_per_row": 2,
                "cards_per_column": 5,
                "design_preset": "modern",
                "font_size": 12,
                "color": "#1f2937",
            },
            follow_redirects=True,
        )

    templates = operations_repo.list_print_templates(1, limit=1000)
    a = next(t for t in templates if t["name"] == name_a)
    b = next(t for t in templates if t["name"] == name_b)

    # Star A
    token = _csrf(client, "/admin/radius/print-templates")
    r1 = client.post(
        f"/admin/radius/print-templates/{a['id']}/set-default",
        data={"_csrf_token": token},
        follow_redirects=False,
    )
    assert r1.status_code in {302, 303}
    assert ops.get_default_print_template_id(tenant_id=1) == a["id"]
    a_after = operations_repo.get_print_template(1, a["id"])
    assert a_after["layout_json"].get("is_default") is True

    # Star B — A must be cleared, B must become the default
    token = _csrf(client, "/admin/radius/print-templates")
    r2 = client.post(
        f"/admin/radius/print-templates/{b['id']}/set-default",
        data={"_csrf_token": token},
        follow_redirects=False,
    )
    assert r2.status_code in {302, 303}
    assert ops.get_default_print_template_id(tenant_id=1) == b["id"]
    assert operations_repo.get_print_template(1, a["id"])["layout_json"].get("is_default") in (False, None)
    assert operations_repo.get_print_template(1, b["id"])["layout_json"].get("is_default") is True

    # The page should advertise the default via the افتراضي badge + the
    # data-is-default attribute the JS uses for auto-select.
    page = client.get("/admin/radius/print-templates")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "افتراضي" in html
    assert f'data-template-id="{b["id"]}"' in html
    assert "data-is-default" in html
