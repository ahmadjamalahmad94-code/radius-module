from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.business_os_finance import EventService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "business_os_sidebar.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "business-os-sidebar-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "business_sidebar"
        sess["admin_name"] = "Business Sidebar"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "business-sidebar-csrf"


def _sidebar(html: str) -> str:
    start = html.index('<aside class="hb-side"')
    end = html.index("</aside>", start)
    return html[start:end]


def test_business_os_sidebar_contains_existing_get_html_routes(app):
    with app.test_client() as client:
        _auth_session(client)
        sidebar = _sidebar(client.get("/admin/radius/dashboard").get_data(as_text=True))

    expected = {
        "نظام الأعمال": "/admin/radius/dashboard",
        "لوحة الأعمال": "/admin/radius/dashboard",
        "المركز المالي": "/admin/radius/finance",
        "المحافظ": "/admin/radius/finance/wallets",
        "الإيرادات": "/admin/radius/finance/revenue",
        "الديون": "/admin/radius/finance/debts",
        "القروض": "/admin/radius/finance/loans",
        "دفتر القيود": "/admin/radius/finance/ledger",
        "المشتركين 360": "/admin/radius/subscribers",
        "مستخدمو البطاقات": "/admin/radius/card-users",
        "سوق البطاقات": "/admin/radius/card-marketplace",
        "تسعير البطاقات": "/admin/radius/card-pricing",
        "المدراء والموزعون": "/admin/radius/business-operators",
        "التواصل والحملات": "/admin/radius/communications",
        "الأحداث والمخاطر": "/admin/radius/events",
        "مركز العمليات": "/admin/radius/operations",
        "التحكم بالسرعة": "/admin/radius/operations/speed-control",
        "التقارير": "/admin/radius/reports",
        "التقرير المالي": "/admin/radius/reports/financial",
        "تقارير البطاقات": "/admin/radius/reports/cards",
        "تقارير الموزعين": "/admin/radius/reports/distributors",
        "الأرشيف": "/admin/radius/reports/archive",
        "بوابات العملاء": "/admin/radius/customer-portals",
    }
    for label, href in expected.items():
        assert label in sidebar
        assert href in sidebar

    for forbidden in ("/api/v1/", "/reports/archive/create", "/apply/", "/rollback/"):
        assert forbidden not in sidebar


def test_business_os_sidebar_referenced_routes_render_html(app):
    with app.app_context():
        event = EventService().record_event(
            tenant_id=1,
            category="security",
            severity="warning",
            event_key="sidebar.route_smoke",
            message="Sidebar route smoke event",
        )
        assert event["id"]

    routes = [
        "/admin/radius/dashboard",
        "/admin/radius/finance",
        "/admin/radius/finance/wallets",
        "/admin/radius/finance/revenue",
        "/admin/radius/finance/debts",
        "/admin/radius/finance/loans",
        "/admin/radius/finance/ledger",
        "/admin/radius/subscribers",
        "/admin/radius/card-users",
        "/admin/radius/card-marketplace",
        "/admin/radius/card-pricing",
        "/admin/radius/business-operators",
        "/admin/radius/communications",
        "/admin/radius/events",
        "/admin/radius/operations",
        "/admin/radius/operations/speed-control",
        "/admin/radius/reports",
        "/admin/radius/reports/financial",
        "/admin/radius/reports/cards",
        "/admin/radius/reports/distributors",
        "/admin/radius/reports/archive",
        "/admin/radius/customer-portals",
    ]
    with app.test_client() as client:
        _auth_session(client)
        failures = []
        for route in routes:
            response = client.get(route)
            if response.status_code != 200 or "text/html" not in response.content_type:
                failures.append((route, response.status_code, response.content_type))

    assert failures == []


def test_customer_portals_admin_index_is_navigation_only(app):
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/customer-portals")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "مساعد تنقل إداري فقط" in html
    assert "/admin/radius/portal/subscriber/login" in html
    assert "/admin/radius/portal/card/login" in html
    assert "/portal/card/purchase" not in html
    assert "/portal/subscriber/loan-request" not in html
