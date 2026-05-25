from __future__ import annotations

import os
import re
import secrets

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "setup_wizard_sidebar.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "sidebar-" + secrets.token_hex(8))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "sidebar_admin"
        sess["admin_name"] = "Sidebar Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "sidebar-csrf"


def _sidebar(html: str) -> str:
    start = html.index('<aside class="hb-side"')
    end = html.index("</aside>", start)
    return html[start:end]


def test_setup_wizard_sidebar_exposes_safe_html_pages_only(app):
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/setup-wizard-v2")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    sidebar = _sidebar(html)
    assert "/admin/radius/setup-wizard-v2" in sidebar
    assert "/admin/radius/setup-wizard/fleet" in sidebar
    assert "/admin/radius/setup-wizard" in sidebar
    assert "الإعداد والتشغيل" in sidebar
    assert "معالج الإعداد" in sidebar
    assert "أسطول الراوترات" in sidebar
    assert "عرض الإعداد الهندسي" in sidebar

    forbidden = (
        "server-peer/apply",
        "server-peer/rollback",
        "/apply/",
        "/rollback/",
        "/api/v1/",
    )
    for token in forbidden:
        assert token not in sidebar


def test_setup_wizard_sidebar_linked_pages_render_html(app):
    routes = [
        "/admin/radius/setup-wizard-v2",
        "/admin/radius/setup-wizard/fleet",
        "/admin/radius/setup-wizard",
    ]
    with app.test_client() as client:
        _auth_session(client)
        for route in routes:
            response = client.get(route)
            assert response.status_code == 200
            assert "text/html" in response.content_type


def test_setup_wizard_active_state_uses_exact_pages(app):
    checks = [
        ("/admin/radius/setup-wizard-v2", "معالج الإعداد"),
        ("/admin/radius/setup-wizard/fleet", "أسطول الراوترات"),
        ("/admin/radius/setup-wizard", "عرض الإعداد الهندسي"),
    ]
    with app.test_client() as client:
        _auth_session(client)
        for route, label in checks:
            sidebar = _sidebar(client.get(route).get_data(as_text=True))
            active_items = re.findall(
                r'<a class="hb-side-subitem is-active".*?</a>',
                sidebar,
                flags=re.S,
            )
            assert len(active_items) == 1
            assert label in active_items[0]
