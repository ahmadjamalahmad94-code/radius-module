"""Web UI smoke tests for card print templates."""
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
    # Designer canvas + form structure (commits 1–10 kept these).
    assert "معاينة بصرية" in page_html or "معاينة حية" in page_html
    assert 'name="card_orientation"' in page_html
    assert 'name="background_image"' in page_html
    assert 'data-drag="username"' in page_html
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
            "background_image": (BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"), "card-bg.png"),
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
    assert "data-export-progress" in export_html
    assert "data-preview-mount" in export_html  # live preview-fragment mount
    assert "pr-designer-drawer" in export_html  # designer collapsed into drawer
    assert name in export_html

    from app.radius.db.repos import operations_repo

    templates = operations_repo.list_print_templates(1, limit=1000)
    template = next(item for item in templates if item["name"] == name)
    layout = template["layout_json"]
    assert layout["card_orientation"] == "vertical"
    assert layout["pattern_style"] == "grid"
    assert layout["background_image_name"] == "card-bg.png"
    assert layout["background_image_data_url"].startswith("data:image/png;base64,")

    preview = client.post(
        f"/admin/radius/print-templates/{template['id']}/preview",
        data={"_csrf_token": token, "sample_username": "QA123"},
        follow_redirects=True,
    )
    assert preview.status_code == 200
    html = preview.get_data(as_text=True)
    assert "visual_card_preview" in html
    assert "pt-visual-card" in html
    assert "QA123" in html

    export = client.get(
        f"/admin/radius/print-templates/{template['id']}/export.pdf",
        query_string={"price_text": "JOD 9", "hotspot_address": "demo.hotspot"},
        follow_redirects=False,
    )
    assert export.status_code == 200
    assert export.content_type.startswith("application/pdf")
    assert export.data.startswith(b"%PDF")


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
