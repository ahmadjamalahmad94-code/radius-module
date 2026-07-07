"""Reports hubs consolidation (R1–R5) — updated 2026-06.

UI-only: navigation lives in the sidebar (5 families × per-report
entries), and every report page carries ONE compact «انتقال سريع»
switcher (reports_nav.html → _partials/report_jump.html) injected in
the hero title row instead of the old two stacked pill bars. All
report queries/filters/exports are untouched, and تقارير المحاسبة
(finance_reports) stays a cross-link to the accounting hub.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "reports_hub.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations

        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "reports_admin"
        sess["admin_name"] = "Reports Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "reports-csrf"


_REPORTS = [
    "/admin/radius/reports",
    "/admin/radius/reports/financial",
    "/admin/radius/reports/cards",
    "/admin/radius/reports/distributors",
    "/admin/radius/reports/archive",
    "/admin/radius/reports/login_states",
    "/admin/radius/reports/failed_logins",
    "/admin/radius/reports/login_status",
    "/admin/radius/reports/manager_login_status",
    "/admin/radius/reports/sessions",
    "/admin/radius/reports/mac_history",
    "/admin/radius/reports/coa_failures",
    "/admin/radius/reports/speed_failures",
    "/admin/radius/reports/manager_events",
    "/admin/radius/reports/system_events",
    "/admin/radius/reports/user_events",
    "/admin/radius/reports/card_store_events",
    "/admin/radius/reports/profile_changes",
    "/admin/radius/reports/api_messages",
    "/admin/radius/reports/used_cards",
    "/admin/radius/reports/cash_transactions",
    "/admin/radius/reports/balance_movements",
]


def test_every_report_page_renders_with_shared_nav(app):
    with app.test_client() as client:
        _auth(client)
        for url in _REPORTS:
            res = client.get(url)
            assert res.status_code == 200, url
            assert 'data-testid="reports-nav"' in res.get_data(as_text=True), url


def test_nav_shows_five_hub_groups(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/reports").get_data(as_text=True)
    for label in ("تنفيذية", "الدخول والأمان", "الشبكة والجلسات",
                  "النشاط والأحداث", "المالية والموازنات"):
        assert label in html, label


def test_active_group_shows_its_report_pills(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/reports/sessions").get_data(as_text=True)
    # the network family's sibling reports appear as options in the
    # compact «انتقال سريع» switcher (and as sidebar entries)
    for label in ("تقارير الجلسات", "سجل تغييرات الماك", "فشل الفصل/التحديث"):
        assert label in html, label


def test_sidebar_groups_reports_into_five_families(app):
    """قرار المشغّل (2026-06): شريطا التبويب الكبيران أُزيلا من رأس صفحات
    التقارير، والتنقّل الكامل انتقل إلى الشريط الجانبي — 5 عوائل بعناوين
    فرعية وكل تقرير بند ظاهر تحت عائلته."""
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    # عناوين العوائل الخمس
    for label in ("التقارير التنفيذية", "الدخول والأمان", "الشبكة والجلسات",
                  "النشاط والأحداث", "المالية والموازنات"):
        assert label in html, label
    # cross-link to the accounting hub stays
    assert "تقارير المحاسبة" in html
    # individual report sub-labels now LIVE in the sidebar (one entry each)
    for label in ("حالات تسجيل الدخول", "سجل تغييرات الماك", "كروت الشحن المستخدمة",
                  "معاملات الكاش", "تحويلات الرصيد", "أحداث المدراء"):
        assert label in html, label


def test_exports_and_archive_post_stay_standalone(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/reports/summary.json" in rules
    assert "/admin/radius/reports/archive/create" in rules
