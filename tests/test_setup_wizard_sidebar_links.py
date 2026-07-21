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
    # حارس دورة حياة الترخيص يقفل اللوحة على قاعدة جديدة بلا لقطة
    # ترخيص؛ تجاوزه في الاختبارات يحتاج العلمين معًا (راجع
    # license_lifecycle._test_bypass_active وتعليق tests/conftest.py).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client, super_admin: bool = True):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "sidebar_admin"
        sess["admin_name"] = "Sidebar Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "sidebar-csrf"
        # «الإعداد الهندسي» صار super_admin فقط (مخفي مؤقتاً بطلب المالك) —
        # الجلسة الافتراضية في هذه الاختبارات super حتى تبقى المسارات القديمة
        # قابلة للزيارة المباشرة (bookmarks).
        sess["is_super_admin"] = super_admin


def _sidebar(html: str) -> str:
    start = html.index('<aside class="hb-side"')
    end = html.index("</aside>", start)
    return html[start:end]


def test_setup_wizard_sidebar_shows_two_consolidated_paths(app):
    # The five overlapping wizard links were consolidated to two clear
    # paths (quick add + advanced). The superseded pages keep their routes
    # but leave the sidebar.
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/setup-wizard-v3")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    sidebar = _sidebar(html)
    assert "الإعداد والتشغيل" in sidebar
    assert "إضافة راوتر (سريع)" in sidebar
    assert "إعداد راوتر متقدم" in sidebar
    # superseded / duplicate labels no longer in the sidebar
    assert "معالج الإعداد" not in sidebar
    assert "أسطول الراوترات" not in sidebar
    assert "عرض الإعداد الهندسي" not in sidebar

    forbidden = (
        "server-peer/apply",
        "server-peer/rollback",
        "/apply/",
        "/rollback/",
        "/api/v1/",
    )
    for token in forbidden:
        assert token not in sidebar


def test_legacy_wizard_pages_still_render_for_bookmarks(app):
    # Routes stay alive even though they left the sidebar.
    routes = [
        "/admin/radius/setup-wizard-v2",
        "/admin/radius/setup-wizard/fleet",
        "/admin/radius/setup-wizard",
    ]
    with app.test_client() as client:
        _auth_session(client)
        for route in routes:
            # Legacy bookmarks stay reachable — either they still render
            # (200) or they redirect to their consolidated home (302, e.g.
            # the fleet shim → router management). Following the redirect
            # must land on a real HTML page, never a 404/500.
            response = client.get(route, follow_redirects=True)
            assert response.status_code == 200, route
            assert "text/html" in response.content_type


def test_engineering_wizard_gated_super_only(app):
    # «الإعداد الهندسي» مخفي مؤقتاً بطلب المالك: المسار يبقى مسجَّلًا
    # (super_admin يفتحه مباشرة)، وغير الـ super يُمنع 403.
    with app.test_client() as client:
        _auth_session(client, super_admin=False)
        response = client.get("/admin/radius/setup-wizard")
        assert response.status_code == 403


def test_engineering_wizard_link_hidden_from_network_nav(app):
    # رابط «الإعداد الهندسي» أزيل من شريط إدارة الراوترات (network_ops_nav).
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert "الإعداد الهندسي" not in html


def test_consolidated_paths_active_state_is_exact(app):
    checks = [
        ("/admin/radius/mt/setup", "إضافة راوتر (سريع)"),
        ("/admin/radius/setup-wizard-v3", "إعداد راوتر متقدم"),
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
            assert len(active_items) == 1, (route, len(active_items))
            assert label in active_items[0]
