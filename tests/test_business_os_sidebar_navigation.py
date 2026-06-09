from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.db.migrations_runner import run_pending_migrations
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

    flask_app = create_app()
    with flask_app.app_context():
        run_pending_migrations()
    return flask_app


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
        "المشتركون": "/admin/radius/subscribers",
        "البطاقات": "/admin/radius/cards/overview",
        "فحص بطاقة": "/admin/radius/cards/checker",
        "المال والتحصيل": "/admin/radius/finance-center",
        "المركز المالي": "/admin/radius/finance-center",
        "السجل والتقارير المحاسبية": "/admin/radius/finance/accounting",
        "المشتركين 360": "/admin/radius/subscribers",
        "مستخدمو البطاقات": "/admin/radius/card-users",
        "سوق البطاقات": "/admin/radius/card-marketplace",
        "المدراء والموزعون": "/admin/radius/business-operators",
        # Communications + events sidebar entries are consolidated: the
        # sub-pages now live in each section's in-section tab bar, not the
        # sidebar. Only the single section entry remains in the sidebar.
        "التواصل والحملات": "/admin/radius/communications",
        "الأحداث والمخاطر": "/admin/radius/events",
        "مركز العمليات": "/admin/radius/operations",
        # قسم «التحكم بالسرعة» المستقل (بلا طبقة فرعية مزدوجة):
        # صفحتان شقيقتان — «مجدول» و«يدوي».
        "التحكم بالسرعة": "/admin/radius/operations/speed-control",
        "مجدول": "/admin/radius/operations/speed-control",
        "يدوي": "/admin/radius/operations/speed-control/manual",
        # Reports are consolidated into 5 hubs; the per-report links live in
        # each report page's two-level in-section nav, not the sidebar.
        "التقارير التنفيذية": "/admin/radius/reports",
        # Login states hub: sidebar entry renamed to the landing page title;
        # the actor sub-pages share the same path via ?actor=… query params.
        "حالات تسجيل الدخول": "/admin/radius/reports/login_states",
        "الشبكة والجلسات": "/admin/radius/reports/sessions",
        "النشاط والأحداث": "/admin/radius/reports/manager_events",
        "المالية والموازنات": "/admin/radius/reports/used_cards",
        "بوابات العملاء": "/admin/radius/customer-portals",
        "التحصيل والمدفوعات": "/admin/radius/finance/collection",
        "إعدادات النظام": "/admin/radius/settings",
        "المزامنة": "/admin/radius/sync",
        "المستأجرون": "/admin/radius/tenants",
    }
    for label, href in expected.items():
        assert label in sidebar
        assert href in sidebar

    assert "/admin/radius/card-pricing" not in sidebar

    for forbidden in ("/api/v1/", "/reports/archive/create", "/apply/", "/rollback/"):
        assert forbidden not in sidebar

    for href in (
        "/admin/radius/finance/accounting",
        "/admin/radius/subscribers",
        "/admin/radius/card-users",
        "/admin/radius/admin-bridge",
    ):
        assert sidebar.count(f'href="{href}"') == 1

    for tone in ("sky", "indigo", "amber", "emerald", "green", "rose", "slate", "cyan", "orange", "blue"):
        assert f'data-hb-tone="{tone}"' in sidebar


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
        "/admin/radius/finance-center",
        "/admin/radius/finance-center?tab=wallets",
        "/admin/radius/finance-center?tab=revenue",
        "/admin/radius/finance-center?tab=loans_debts&status=open",
        "/admin/radius/finance-center?tab=loans_debts",
        "/admin/radius/finance/accounting",
        "/admin/radius/subscribers",
        "/admin/radius/cards/checker",
        "/admin/radius/card-users",
        "/admin/radius/card-marketplace",
        "/admin/radius/business-operators",
        "/admin/radius/communications",
        "/admin/radius/communications/templates",
        "/admin/radius/communications/send",
        "/admin/radius/communications/campaigns",
        "/admin/radius/communications/deliveries",
        "/admin/radius/communications/audience",
        "/admin/radius/events",
        "/admin/radius/events/risk",
        "/admin/radius/events/security",
        "/admin/radius/events/investigations",
        "/admin/radius/operations",
        "/admin/radius/operations/speed-control",
        "/admin/radius/operations/speed-control/manual",
        "/admin/radius/reports",
        "/admin/radius/reports/financial",
        "/admin/radius/reports/login_states",
        "/admin/radius/reports/sessions",
        "/admin/radius/reports/failed_logins",
        "/admin/radius/reports/login_status",
        "/admin/radius/reports/manager_login_status",
        "/admin/radius/reports/mac_history",
        "/admin/radius/reports/coa_failures",
        "/admin/radius/reports/manager_events",
        "/admin/radius/reports/user_events",
        "/admin/radius/reports/profile_changes",
        "/admin/radius/reports/api_messages",
        "/admin/radius/reports/cards",
        "/admin/radius/reports/distributors",
        "/admin/radius/reports/archive",
        "/admin/radius/customer-portals",
        "/admin/radius/finance/collection",
        "/admin/radius/finance/collection?tab=review",
        "/admin/radius/finance/collection?tab=reconciliation",
        "/admin/radius/finance/collection?tab=settings",
        "/admin/radius/settings",
        "/admin/radius/sync",
        "/admin/radius/tenants",
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
