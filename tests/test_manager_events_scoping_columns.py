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
        from app.radius.db.connection import db
        from app.radius.services.audit import get_audit_service
        from app.radius.services.login_events import record_login_event
        aud = get_audit_service()
        # عميل متجر بطاقات حقيقيّ (لحلّ الهويّة بدل معرّف رقميّ خام)
        cur = db().execute(
            "INSERT INTO card_users(tenant_id, display_name, mobile, created_at) "
            "VALUES(1, ?, ?, datetime('now'))", ("أحمد العميل", CARD_CUSTOMER))
        cu_id = int(cur.lastrowid)
        db().commit()
        # دخول ناجح: يُخزَّن target_id = معرّف card_user (رقم) والفاعل = الجوّال →
        # يجب أن يُحلّ للاسم/الجوّال لا للرقم الخام.
        record_login_event(actor_type="card", username=CARD_CUSTOMER,
                           success=True, actor_id=cu_id, tenant_id=1)
        # محاولة فاشلة → تسمية «محاولة فاشلة» + سبب
        record_login_event(actor_type="card", username=CARD_CUSTOMER,
                           success=False, reason="bad_password", tenant_id=1)
        # تغيير باقة مشترك (plan) → profile_changes + manager_events، لا user_events
        aud.record(actor="manager_bob", action="update", target_type="user",
                   target_id="SUB_PKG", before={"plan": "الادارة"},
                   after={"plan": "طلاب"})
        # تعديل اسم/جوّال فقط (بلا باقة) → manager_events فقط، لا profile_changes
        aud.record(actor="manager_bob", action="update", target_type="user",
                   target_id="SUB_NAME", before={"full_name": "أحمد", "mobile": "000000"},
                   after={"full_name": "أحمد ٢", "mobile": "111111"})
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
        # فاعل عامّ غير بشريّ 'ui' + قالب دخول بفرق (slug + متغيّرات) → النظام.
        # يجب أن تُترجَم: slug→اسم عربيّ، مفاتيح المتغيّرات→تسميات عربيّة.
        aud.record(actor="ui", action="update", target_type="login_template",
                   target_id="UI_TMPL",
                   before={"template_slug": "morning_coffee",
                           "variables": {"WELCOME_TEXT": "قديم"}},
                   after={"template_slug": "espresso_lux",
                          "variables": {"WELCOME_TEXT": "أهلًا", "TENANT_NAME": "متجري"}})
        # مهمّة السرعة المؤقتة الآليّة → «النظام: السرعة المؤقتة» (لا «temp speed»)
        aud.record(actor="system:temp-speed", action="update",
                   target_type="subscriber", target_id="TS1",
                   payload={"status": "ok"})
        # نسخة مجدولة حقيقيّة: target_id = معرّف مهمّة رقميّ (كان «ملف: 1»)
        aud.record(actor="system:backup-scheduler", action="backup.local_run",
                   target_type="backup_job", target_id="1",
                   payload={"status": "ok", "verified": True})
        # تنظيف نسخ قديمة: target_id = أيّام الاحتفاظ (كان «ملف: 60»)
        aud.record(actor="system", action="backup.local_pruned",
                   target_type="backup_retention", target_id="60",
                   payload={"removed": ["a.db", "b.db", "c.db"], "retention_days": 60})


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
    # أفعال المدير البشريّ (عبر أقسام مختلفة) تظهر — بما فيها تعديل الاسم فقط
    assert "OFR77" in html and "SUB_PKG" in html and "SUB_DIS" in html
    assert "SUB_NAME" in html, "name-only edit must appear in manager_events"
    assert "BK_HUMAN" in html, "human backup-trigger must stay in manager_events"
    # النظام والعملاء والفاعل العامّ 'ui' لا يظهرون
    assert "BK_SYS" not in html, "system:backup-scheduler leaked into manager_events"
    assert "UI_TMPL" not in html, "actor='ui' leaked into manager_events"
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
    # الفاعل العامّ 'ui' يُصنَّف نظامًا ويظهر هنا
    assert "UI_TMPL" in sys_html, "actor='ui' should be routed to system_events"
    # النسخة اليدويّة (فاعل بشريّ) ليست هنا
    assert "BK_HUMAN" not in sys_html


# ─── لا قيَم غامضة: ملخّصات مفهومة + فاعلون معرّفون ───

def test_system_backup_summary_human_readable(app):
    _seed(app)
    sys_html = _get(app, "/admin/radius/reports/system_events")
    # لا «ملف: <رقم داخليّ>» غامض (كان معرّف مهمّة/أيّام احتفاظ)
    assert "ملف: 1" not in sys_html and "ملف: 60" not in sys_html
    # ملخّصات عربيّة مفهومة بدلها
    assert "تشغيل نسخة احتياطية" in sys_html
    assert "تنظيف النسخ الاحتياطية" in sys_html


# رموز إنجليزيّة/آليّة يجب ألّا تظهر في النصّ المرئيّ لأيّ عمود.
_RAW_TOKENS = [
    "temp speed", "backup scheduler", "backup retention",   # فاعل/كيان مُؤنَّس
    "WELCOME_TEXT", "TENANT_NAME", "TENANT_LOGO_URL",        # مفاتيح متغيّرات
    "espresso_lux", "morning_coffee",                        # slug قالب خام
]


def test_no_english_machine_tokens_in_system_events(app):
    _seed(app)
    html = _get(app, "/admin/radius/reports/system_events")
    for tok in _RAW_TOKENS:
        assert tok not in html, f"raw English token leaked into system_events: {tok!r}"
    # البدائل العربيّة المتوقّعة
    assert "النظام: السرعة المؤقتة" in html          # actor system:temp-speed
    assert "النظام: مجدول النسخ الاحتياطي" in html    # actor system:backup-scheduler
    assert "الاحتفاظ بالنسخ الاحتياطية" in html       # entity backup_retention
    assert "البنّي الفاخر" in html                    # slug espresso_lux → اسم
    assert "نص الترحيب" in html                       # var key WELCOME_TEXT → تسمية


def test_no_cryptic_actor_codes(app):
    _seed(app)
    sys_html = _get(app, "/admin/radius/reports/system_events")
    # 'ui' يُعرَض كعنصر نائب واضح، والمجدول باسم مفهوم — لا رموز خام
    assert "عملية واجهة" in sys_html, "actor='ui' shown as raw code"
    assert "النظام:" in sys_html, "system:scheduler shown as raw code"


# ─── profile_changes = تغييرات الباقات فقط ───

def test_profile_changes_package_only(app):
    _seed(app)
    prof = _get(app, "/admin/radius/reports/profile_changes")
    mgr = _get(app, "/admin/radius/reports/manager_events")
    # تغيير الباقة يظهر في تغييرات الباقات
    assert "SUB_PKG" in prof, "package change missing from profile_changes"
    # تعديل الاسم/الجوّال فقط لا يظهر هنا، لكنّه في أحداث المدراء
    assert "SUB_NAME" not in prof, "name-only edit leaked into profile_changes"
    assert "SUB_NAME" in mgr, "name-only edit should appear in manager_events"
    # دورة الحياة (disable) ليست تغيير باقة
    assert "SUB_DIS" not in prof


def test_profile_changes_and_user_events_are_disjoint(app):
    _seed(app)
    prof = _get(app, "/admin/radius/reports/profile_changes")
    usr = _get(app, "/admin/radius/reports/user_events")
    # تغيير الباقة في تغييرات الباقات فقط
    assert "SUB_PKG" in prof and "SUB_PKG" not in usr
    # دورة الحياة (disable) في أحداث المستخدمين فقط
    assert "SUB_DIS" in usr and "SUB_DIS" not in prof


# ─── card_store_events ───

def test_card_login_in_card_store_only(app):
    _seed(app)
    store = _get(app, "/admin/radius/reports/card_store_events")
    assert CARD_CUSTOMER in store
    assert "OFR77" not in store


def test_card_store_identity_resolves_and_result_labeled(app):
    _seed(app)
    store = _get(app, "/admin/radius/reports/card_store_events")
    # الهويّة تُحلّ للاسم الحقيقيّ (بدل معرّف رقميّ خام مثل «4»)
    assert "أحمد العميل" in store, "card-store identity not resolved to real name"
    # نتيجة واضحة لكل صفّ
    assert "دخول ناجح" in store, "success label missing"
    assert "محاولة فاشلة" in store, "fail label missing"


# ─── أعمدة قبل/بعد: فقط حيث للأحداث فرق حقليّ ───

def _has_change_cols(html: str) -> bool:
    # الإشارة الحقيقيّة = خلايا الأعمدة المنفصلة (chg-c-*). لا نعتمد على نصّ
    # «القيمة السابقة» لأنّه موجود دائمًا في تعليق CSS للمكوّن المشترك.
    return ("chg-c-field" in html and "chg-c-old" in html
            and "chg-c-new" in html)


@pytest.mark.parametrize("url", [
    "/admin/radius/reports/manager_events",
    "/admin/radius/reports/profile_changes",
])
def test_change_columns_present_where_relevant(app, url):
    _seed(app)
    assert _has_change_cols(_get(app, url)), url


@pytest.mark.parametrize("url", [
    "/admin/radius/reports/user_events",
    "/admin/radius/reports/system_events",
    "/admin/radius/reports/card_store_events",
])
def test_change_columns_absent_where_irrelevant(app, url):
    _seed(app)
    html = _get(app, url)
    assert not _has_change_cols(html), url
    # لكن يبقى عمود «التفاصيل» المسطّح
    assert "التفاصيل" in html, url
