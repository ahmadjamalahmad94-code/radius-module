"""عدّادا «تحميل/رفع» في قائمة المشتركين — من `radacct` لا من عمودٍ ميّت.

🔴 المكتشَف يوم 2026-09-09 (بلاغ سمير: «ليش العدادات صفر»): العمودان
`subscribers.used_bytes_in/out` **لا يكتبهما أحدٌ لمشتركٍ حقيقيّ** — كاتبُهما
الوحيدُ بذرةُ العرض التجريبيّ ومسارُ البطاقات. فظلّا صفرًا في كلّ نسخةٍ
منذ البداية (تحقّقتُ: عبد 0/8052، فادي 0/66957، سمير 0/1485) بينما
`radacct` يحمل مئاتِ الجيجابايت.

والاتّجاه بحسب RFC 2866: `acctinputoctets` = رفع، `acctoutputoctets` = تنزيل.

شغّل هذا الملفّ وحدَه (عزلُ الاختبارات لكلّ ملف).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_bytes_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


MB = 1048576


def _seed(app, sessions):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        db().execute(
            "INSERT INTO subscribers(tenant_id,username,password,status,"
            "user_type,created_at) VALUES(1,'bob','p','enabled','subscriber',"
            "datetime('now'))")
        for i, (inb, outb) in enumerate(sessions):
            db().execute(
                "INSERT INTO radacct(tenant_id,username,acctsessionid,"
                " acctstarttime,acctsessiontime,acctinputoctets,"
                " acctoutputoctets) VALUES(1,'bob',?,datetime('now'),60,?,?)",
                ("s%d" % i, inb, outb))
        db().commit()


def _auth(client):
    with client.session_transaction() as s:
        s.update(admin_id=1, admin_user="t", admin_name="t",
                 is_super_admin=True, tenant_id=1)


def test_sums_all_sessions_with_rfc2866_direction(app):
    _seed(app, [(1 * MB, 10 * MB), (2 * MB, 20 * MB)])
    with app.app_context():
        from app.radius.services.usage_counters import bytes_by_username
        assert bytes_by_username(1, ["bob"]) == {"bob": (3 * MB, 30 * MB)}


def test_unknown_names_and_empty_input_are_safe(app):
    _seed(app, [(MB, MB)])
    with app.app_context():
        from app.radius.services.usage_counters import bytes_by_username
        assert bytes_by_username(1, []) == {}
        assert "ghost" not in bytes_by_username(1, ["ghost"])


def test_other_tenant_rows_are_not_counted(app):
    """شبكةٌ لا ترى استهلاكَ شبكةٍ أخرى تشاركها الاسمَ نفسَه."""
    _seed(app, [(MB, MB)])
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.usage_counters import bytes_by_username
        # شبكةٌ ثانيةٌ حقيقيّة — القيدُ الأجنبيّ يرفض مستأجرًا وهميًّا
        db().execute(
            "INSERT INTO tenants(name, slug, created_at)"
            " VALUES('ثانية','t2',datetime('now'))")
        tid2 = int(db().execute(
            "SELECT last_insert_rowid()").fetchone()[0])
        db().execute(
            "INSERT INTO subscribers(tenant_id,username,password,status,"
            "user_type,created_at) VALUES(?,'bob','p','enabled',"
            "'subscriber',datetime('now'))", (tid2,))
        db().execute(
            "INSERT INTO radacct(tenant_id,username,acctsessionid,"
            " acctstarttime,acctsessiontime,acctinputoctets,acctoutputoctets)"
            " VALUES(?,'bob','x',datetime('now'),60,?,?)",
            (tid2, 99 * MB, 99 * MB))
        db().commit()
        assert bytes_by_username(1, ["bob"]) == {"bob": (MB, MB)}


def test_list_page_shows_the_real_numbers(app):
    """البرهانُ الحيّ: الصفحةُ المرسومةُ تحمل الرقمَ الحقيقيَّ لا صفرًا."""
    _seed(app, [(3 * MB, 30 * MB)])
    with app.test_client() as c:
        _auth(c)
        res = c.get("/admin/radius/subscribers")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "30.0 MB" in html, "التنزيل ما زال صفرًا"
        assert "3.0 MB" in html, "الرفع ما زال صفرًا"
        assert 'data-col="download" data-sort-value="%d"' % (30 * MB) in html
        assert 'data-col="upload" data-sort-value="%d"' % (3 * MB) in html


def test_stored_columns_are_still_the_fallback(app):
    """لا نكسر نسخةً تملأ العمودَين فعلًا: بلا صفوف radacct تُقرأ المخزَّنة."""
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        db().execute(
            "INSERT INTO subscribers(tenant_id,username,password,status,"
            "user_type,used_bytes_in,used_bytes_out,created_at)"
            " VALUES(1,'stored','p','enabled','subscriber',?,?,"
            "datetime('now'))", (5 * MB, 50 * MB))
        db().commit()
    with app.test_client() as c:
        _auth(c)
        html = c.get("/admin/radius/subscribers").get_data(as_text=True)
        assert "50.0 MB" in html and "5.0 MB" in html
