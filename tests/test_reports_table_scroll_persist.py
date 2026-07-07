"""جداول التقارير: تمرير أفقيّ + حفظ رؤية الأعمدة (المكوّن المشترك uds).

BUG1: `.uds-table-wrap` صار overflow-x:auto فتُمرَّر الجداول العريضة أفقيًّا.
BUG2: كل صفحة تقرير تحمل data-uds-persist فيَحفظ uds_table.js رؤية الأعمدة
      في localStorage (مفتاح لكلّ تقرير) ويستعيدها بعد إعادة التحميل.

اختبار HTML/الأصول (لا متصفّح): نتحقّق من ظهور الغلاف + سمة الحفظ على الصفحات
الأربع، ومن قاعدة CSS التمرير، ومن وجود منطق الحفظ في الـJS.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tbl_ux.db")
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


def _seed(app):
    """صفٌّ لكلّ صفحة كي يُرندَر الجدول (القوالب ترسم الجدول فقط مع items)."""
    with app.app_context():
        from app.radius.services.audit import get_audit_service
        from app.radius.services.login_events import record_login_event
        aud = get_audit_service()
        # تغيير باقة مشترك → manager_events + profile_changes (تغييرات الباقات)
        aud.record(actor="mgr", action="update", target_type="user",
                   target_id="sub1", before={"plan": "الادارة"},
                   after={"plan": "طلاب"})
        # دورة حياة مشترك → manager_events + user_events (لا profile_changes)
        aud.record(actor="mgr", action="disable", target_type="user",
                   target_id="sub2", payload={"username": "sub2"})
        # مهمّة نظام آليّة → system_events
        aud.record(actor="system:backup-scheduler", action="backup_create",
                   target_type="backup", target_id="bk1",
                   payload={"filename": "auto.tgz"})
        # دخول عميل متجر البطاقات → card_store_events
        record_login_event(actor_type="card", username="0599043337",
                           success=True, tenant_id=1)


_PAGES = {
    "/admin/radius/reports/manager_events":   "manager_events",
    "/admin/radius/reports/system_events":    "system_events",
    "/admin/radius/reports/user_events":      "user_events",
    "/admin/radius/reports/profile_changes":  "profile_changes",
    "/admin/radius/reports/card_store_events": "card_store_events",
}


@pytest.mark.parametrize("url,persist_id", list(_PAGES.items()))
def test_report_table_has_scroll_wrapper_and_persist_id(app, url, persist_id):
    _seed(app)
    with app.test_client() as client:
        _auth(client)
        res = client.get(url)
        assert res.status_code == 200, url
        html = res.get_data(as_text=True)
    # الغلاف القابل للتمرير موجود
    assert "uds-table-wrap" in html, url
    # سمة الحفظ لكلّ تقرير موجودة بالمعرّف الصحيح
    assert 'data-uds-persist="%s"' % persist_id in html, url


def test_css_enables_horizontal_scroll(app):
    css = os.path.join(app.static_folder, "css", "unified_design.css")
    with open(css, encoding="utf-8") as fh:
        text = fh.read()
    # لم يعُد overflow-x:hidden — صار auto (تمرير أفقيّ RTL-صحيح)
    assert ".uds-table-wrap{ overflow-x: auto" in text
    assert ".uds-table-wrap{ overflow-x: hidden" not in text


def test_js_persists_column_visibility(app):
    js = os.path.join(app.static_folder, "js", "uds_table.js")
    with open(js, encoding="utf-8") as fh:
        text = fh.read()
    # منطق الحفظ موجود: مفتاح لكل صفحة + قراءة/كتابة localStorage
    assert "data-uds-persist" in text
    assert "readHiddenLabels" in text and "saveHiddenLabels" in text
    assert "localStorage" in text
    assert "persistCols" in text
