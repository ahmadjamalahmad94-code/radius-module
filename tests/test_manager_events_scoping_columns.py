"""عدسات تقارير الأحداث المتمايزة + فصل النظام + الأعمدة الثلاثة.

  • manager_events = السجل الرئيسي بالفاعل البشريّ عبر كل الأقسام (يستبعد
    api-token، وكلّ system%، ودخول العملاء).
  • system_events  = الفاعل الآليّ system% فقط.
  • profile_changes = target_type=user AND action IN (update,extend_time).
  • user_events     = target_type=user AND action NOT IN (update,extend_time).
    → profile_changes و user_events **منفصلتان تمامًا** (لا صفوف مشتركة).
  • card_store_events = دخول عملاء متجر البطاقات.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "lenses.db")
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
    with app.app_context():
        from app.radius.services.audit import get_audit_service
        from app.radius.services.login_events import record_login_event
        aud = get_audit_service()
        # دخول عميل متجر البطاقات → card_store_events فقط
        record_login_event(actor_type="card", username=CARD_CUSTOMER,
                           success=True, tenant_id=1)
        # تعديل حقل مشترك (update) → profile_changes + manager_events، لا user_events
        aud.record(actor="manager_bob", action="update", target_type="user",
                   target_id="SUB_UPD", before={"mobile": "000000"},
                   after={"mobile": "111111"})
        # دورة حياة مشترك (disable) → user_events + manager_events، لا profile_changes
        aud.record(actor="manager_bob", action="disable", target_type="user",
                   target_id="SUB_DIS", payload={"username": "SUB_DIS"})
        # تعديل عرض بفرق سعر (لاختبار الأعمدة) → manager_events
        aud.record(actor="manager_bob", action="update", target_type="offer",
                   target_id="OFR77", payload={"name": "عرض الاختبار"},
                   before={"selling": "5.00"}, after={"selling": "9.50"})
        # مهمّة آليّة مجدولة → system_events، لا manager_events
        aud.record(actor="system:backup-scheduler", action="backup_create",
                   target_type="backup", target_id="BK_SYS",
                   payload={"filename": "auto.tgz"})
        # نسخة احتياطية شغّلها مدير بشريّ يدويًّا → تبقى في manager_events
        aud.record(actor="manager_bob", action="backup_create",
                   target_type="backup", target_id="BK_HUMAN",
                   payload={"filename": "manual.tgz"})


def _get(app, url):
    with app.test_client() as client:
        _auth(client)
        res = client.get(url)
        assert res.status_code == 200, url
        return res.get_data(as_text=True)


# ─── manager_events: بشريّ عبر كل الأقسام، بلا نظام/عملاء ───

def test_manager_events_human_only(app):
    _seed(app)
    html = _get(app, "/admin/radius/reports/manager_events")
    # أفعال المدير البشريّ (عبر أقسام مختلفة) تظهر
    assert "OFR77" in html and "SUB_UPD" in html and "SUB_DIS" in html
    assert "BK_HUMAN" in html, "human backup-trigger must stay in manager_events"
    # النظام والعملاء لا يظهرون
    assert "BK_SYS" not in html, "system:backup-scheduler leaked into manager_events"
    assert CARD_CUSTOMER not in html, "card login leaked into manager_events"


def test_manager_events_three_change_columns(app):
    _seed(app)
    html = _get(app, "/admin/radius/reports/manager_events")
    assert "القيمة السابقة" in html and "القيمة الجديدة" in html and "الحقل" in html
    assert "chg-c-field" in html and "chg-c-old" in html and "chg-c-new" in html
    assert "سعر البيع" in html and "5.00" in html and "9.50" in html


# ─── system_events: الفاعل الآليّ فقط ───

def test_system_events_separation(app):
    _seed(app)
    sys_html = _get(app, "/admin/radius/reports/system_events")
    assert "BK_SYS" in sys_html, "system job missing from system_events"
    # النسخة اليدويّة (فاعل بشريّ) ليست هنا
    assert "BK_HUMAN" not in sys_html


# ─── profile_changes ⟂ user_events (لا صفوف مشتركة) ───

def test_profile_changes_and_user_events_are_disjoint(app):
    _seed(app)
    prof = _get(app, "/admin/radius/reports/profile_changes")
    usr = _get(app, "/admin/radius/reports/user_events")
    # التعديل (update) في تغييرات الباقات فقط
    assert "SUB_UPD" in prof and "SUB_UPD" not in usr
    # دورة الحياة (disable) في أحداث المستخدمين فقط
    assert "SUB_DIS" in usr and "SUB_DIS" not in prof


# ─── card_store_events ───

def test_card_login_in_card_store_only(app):
    _seed(app)
    store = _get(app, "/admin/radius/reports/card_store_events")
    assert CARD_CUSTOMER in store
    assert "OFR77" not in store


# ─── الأعمدة الثلاثة تُرندَر على الصفحات الأربع ───

@pytest.mark.parametrize("url", [
    "/admin/radius/reports/manager_events",
    "/admin/radius/reports/system_events",
    "/admin/radius/reports/user_events",
    "/admin/radius/reports/profile_changes",
])
def test_three_columns_headers_on_all_pages(app, url):
    _seed(app)
    html = _get(app, url)
    assert "الحقل" in html and "القيمة السابقة" in html and "القيمة الجديدة" in html, url
