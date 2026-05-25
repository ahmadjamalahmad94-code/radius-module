from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "admin_bridge_sidebar.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "admin-bridge-sidebar-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "false")
    monkeypatch.setenv("HOBERADIUS_ADMIN_SHARED_SECRET", "super-secret-test-value")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "license-secret-test-value")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "bridge_sidebar"
        sess["admin_name"] = "Bridge Sidebar"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "bridge-sidebar-csrf"


def _sidebar(html: str) -> str:
    start = html.index('<aside class="hb-side"')
    end = html.index("</aside>", start)
    return html[start:end]


def test_admin_bridge_sidebar_contains_safe_html_page_only(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/admin-bridge").get_data(as_text=True)

    sidebar = _sidebar(html)
    assert "جسر الإدارة V40" in sidebar
    assert "/admin/radius/admin-bridge" in sidebar
    assert "/api/v1/system/admin-bridge" not in sidebar
    for forbidden in ("server-peer/apply", "server-peer/rollback", "restore-apply", "/api/v1/", "upload-latest"):
        assert forbidden not in sidebar


def test_admin_bridge_index_returns_html_with_dry_run_safety_labels(app):
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/admin-bridge")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert "وضع جاف" in html
    assert "غير مفعل إنتاجيًا" in html
    assert "يحتاج تأكيد عقود الإدارة" in html
    assert "super-secret-test-value" not in html
    assert "license-secret-test-value" not in html


def test_admin_bridge_index_does_not_expose_live_action_links(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/admin-bridge").get_data(as_text=True)

    forbidden = (
        "server-peer/apply",
        "server-peer/rollback",
        "/apply/",
        "/rollback/",
        "restore-apply",
        "upload-latest",
        "wg set",
        "systemctl restart",
    )
    for token in forbidden:
        assert token not in html
