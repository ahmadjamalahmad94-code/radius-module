"""Web UI smoke tests for card print templates."""
from __future__ import annotations

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
    assert "معاينة بصرية" in page.get_data(as_text=True)

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
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert name in created.get_data(as_text=True)

    from app.radius.db.repos import operations_repo

    templates = operations_repo.list_print_templates(1, limit=1000)
    template = next(item for item in templates if item["name"] == name)

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
        follow_redirects=False,
    )
    assert export.status_code == 200
    assert export.content_type.startswith("application/pdf")
    assert export.data.startswith(b"%PDF")
