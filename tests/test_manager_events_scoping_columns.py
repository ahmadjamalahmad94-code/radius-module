"""أحداث المدراء = عمليّات بشريّة فقط + الأعمدة الثلاثة (الحقل/سابقة/جديدة).

  1) دخول عميل متجر البطاقات (record_login_event actor_type='card') **يُستبعَد**
     من manager_events، بينما فعل مدير حقيقيّ **يظهر**.
  2) نفس دخول العميل **يظهر** في السجلّ الجديد card_store_events.
  3) الصفحات الثلاث تعرض الأعمدة المنفصلة «الحقل/القيمة السابقة/القيمة الجديدة».
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mgr_scope.db")
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
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "root"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


CARD_CUSTOMER = "0599043337"


def _seed(app):
    """يزرع: دخول عميل متجر (بطاقة) + فعل مدير حقيقيّ بفرق حقول."""
    with app.app_context():
        from app.radius.services.login_events import record_login_event
        from app.radius.services.audit import get_audit_service
        # دخول عميل متجر البطاقات → auth_login, target_type='card'
        record_login_event(actor_type="card", username=CARD_CUSTOMER,
                           success=True, tenant_id=1)
        # فعل مدير حقيقيّ: تعديل عرض بفرق حقول (سعر البيع)
        get_audit_service().record(
            actor="manager_bob", action="update", target_type="offer",
            target_id="OFR77", payload={"name": "عرض الاختبار"},
            before={"selling": "5.00"}, after={"selling": "9.50"})


def test_card_login_excluded_but_manager_action_included(app):
    _seed(app)
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/reports/manager_events").get_data(as_text=True)
    # فعل المدير يظهر
    assert "OFR77" in html, "manager action missing from manager_events"
    # دخول عميل المتجر لا يظهر
    assert CARD_CUSTOMER not in html, "card-store login leaked into manager_events"


def test_card_login_appears_in_card_store_events(app):
    _seed(app)
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/reports/card_store_events")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    assert CARD_CUSTOMER in html, "card login missing from card_store_events"
    # وهنا لا يظهر فعل المدير (سجلّ العملاء فقط)
    assert "OFR77" not in html


def test_manager_events_three_change_columns(app):
    _seed(app)
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/reports/manager_events").get_data(as_text=True)
    # رؤوس الأعمدة الثلاثة
    assert "القيمة السابقة" in html and "القيمة الجديدة" in html and "الحقل" in html
    # خلايا الأعمدة المنفصلة + قيَم الفرق
    assert "chg-c-field" in html and "chg-c-old" in html and "chg-c-new" in html
    assert "سعر البيع" in html and "5.00" in html and "9.50" in html


@pytest.mark.parametrize("url", [
    "/admin/radius/reports/user_events",
    "/admin/radius/reports/profile_changes",
])
def test_three_columns_headers_on_all_pages(app, url):
    _seed(app)
    with app.test_client() as client:
        _auth(client)
        res = client.get(url)
        assert res.status_code == 200, url
        html = res.get_data(as_text=True)
    assert "الحقل" in html and "القيمة السابقة" in html and "القيمة الجديدة" in html, url
