# -*- coding: utf-8 -*-
"""تقرير استهلاك المشتركين: التصيير، صحّة التجميع (KPI + الأعلى + الإجمالي
+ تنزيل/رفع)، رابط السايدبار تحت «المشتركون»، التصدير ينتج ملفًا، فلتر
التاريخ، والحالة الخالية بلا بيانات. شغّل الملف وحده."""
from __future__ import annotations

import json
import os

import pytest

GB = 1073741824
PATH = "/admin/radius/reports/subscriber-consumption"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "usage.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "usage_admin"
        s["admin_name"] = "Usage Admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "usage-csrf"


def _seed(app, *, alpha_active=True):
    """مشتركان معروفا الاستهلاك:
      ألفا: تنزيل 3GB + رفع 1GB  (الإجمالي 4GB) — الأعلى.
      بيتا: تنزيل 1GB + رفع 0.5GB (الإجمالي 1.5GB).
    الإجماليات: تنزيل 4GB، رفع 1.5GB، الإجمالي 5.5GB، مستهلكان."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO access_plans(id, tenant_id, name, created_at) "
                      "VALUES(1,1,?,?)", ("الباقة الذهبية", "2026-01-01"))
            c.execute("INSERT INTO subscribers(tenant_id, username, full_name, "
                      "mobile, plan_id, status, created_at) "
                      "VALUES(1,?,?,?,1,'enabled','2026-01-01')",
                      ("alpha", "ألفا حسن", "0590000001"))
            c.execute("INSERT INTO subscribers(tenant_id, username, full_name, "
                      "mobile, plan_id, status, created_at) "
                      "VALUES(1,?,?,?,1,'enabled','2026-01-01')",
                      ("beta", "بيتا علي", "0590000002"))

            def acct(user, sid, start, stop, din, dout):
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, username, "
                    "nasipaddress, acctstarttime, acctstoptime, acctsessiontime, "
                    "acctinputoctets, acctoutputoctets) VALUES(1,?,?,?,?,?,?,?,?)",
                    (sid, user, "10.0.0.1", start, stop, 3600, din, dout))
            # ألفا: جلستان داخل النطاق (تنزيل 2+1=3GB، رفع 1GB).
            acct("alpha", "a1", "2026-06-01 09:00:00",
                 None if alpha_active else "2026-06-01 10:00:00", 2 * GB, 1 * GB)
            acct("alpha", "a2", "2026-06-02 09:00:00", "2026-06-02 10:00:00", 1 * GB, 0)
            # بيتا: جلسة واحدة (تنزيل 1GB، رفع 0.5GB).
            acct("beta", "b1", "2026-06-01 11:00:00", "2026-06-01 12:00:00",
                 1 * GB, GB // 2)


# ── (1) التصيير ──
def test_page_renders(app):
    c = app.test_client()
    _seed(app)
    _auth(c)
    res = c.get(PATH)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "تقرير استهلاك المشتركين" in html
    assert 'data-testid="usage-hero"' in html
    assert 'data-testid="usage-charts"' in html
    # جدول uds + أعمدته الوظيفية.
    assert "data-uds-table" in html
    for col in ("المستخدم", "الاسم الكامل", "الجوال", "الباقة", "تنزيل", "رفع", "الإجمالي"):
        assert col in html


# ── (2) صحّة التجميع: KPI + الأعلى + الإجمالي + تنزيل/رفع ──
def test_aggregation_math(app):
    c = app.test_client()
    _seed(app)
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    # إجمالي الاستهلاك = 5.50 GB (KPI الرئيسي).
    assert "5.50 GB" in html
    # الأعلى استهلاكًا = ألفا (4GB).
    assert "ألفا حسن" in html
    assert "4.00 GB" in html      # إجمالي ألفا
    # بيتا حاضر بإجماليه (1.5GB).
    assert "بيتا علي" in html
    assert "1.50 GB" in html
    # عدد المشتركين = 2، ومتصل الآن = 1 (جلسة ألفا النشطة).
    assert "متصل الآن: 1" in html
    # الباقة تظهر.
    assert "الباقة الذهبية" in html


def test_top_consumer_order(app):
    """أعلى مستهلك أوّلًا في الجدول + ظاهر في عمود الرسم."""
    c = app.test_client()
    _seed(app)
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    # ألفا يَسبق بيتا (ترتيب تنازلي بالإجمالي).
    assert html.index("ألفا حسن") < html.index("بيتا علي")


# ── (3) رابط السايدبار تحت «المشتركون» ──
def test_sidebar_link_present(app):
    c = app.test_client()
    _seed(app)
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert PATH in html               # الرابط حاضر
    assert "تقرير الاستهلاك" in html  # تسمية عنصر السايدبار
    # ضمن قسم المشتركين (يظهر عنوان القسم في نفس الصفحة).
    assert "المشتركون" in html


def test_perm_guard_wired():
    """الصفحة محروسة بصلاحية «reports.view» (نفس آلية بقية التقارير،
    والمدير الرئيسي يتجاوزها)."""
    from app.radius.routes import blueprint as bp
    assert bp._PERM_GUARDED.get("rep_subscriber_consumption") == "reports.view"


# ── (4) التصدير ينتج ملفًا (المسار المشترك الذي يستعمله الجدول) ──
def test_export_produces_file(app):
    c = app.test_client()
    _auth(c)
    payload = {
        "_csrf_token": "usage-csrf",
        "title": "استهلاك المشتركين",
        "fmt": "csv",
        "columns": json.dumps(["المستخدم", "الإجمالي"]),
        "rows": json.dumps([["alpha", "4.00 GB"], ["beta", "1.50 GB"]]),
    }
    res = c.post("/admin/radius/export/table", data=payload)
    assert res.status_code == 200
    cd = res.headers.get("Content-Disposition", "")
    assert ".csv" in cd
    body = res.get_data(as_text=True)
    assert "alpha" in body and "4.00 GB" in body


# ── (5) فلتر التاريخ يحصر التجميع ──
def test_date_range_filter(app):
    c = app.test_client()
    _seed(app)
    _auth(c)
    # نطاق 2026-06-02 فقط → ألفا جلسة a2 (1GB) فقط، بيتا خارج النطاق.
    html = c.get(PATH + "?date_from=2026-06-02&date_to=2026-06-02").get_data(as_text=True)
    assert "بيتا علي" not in html            # خارج النطاق
    assert "1.00 GB" in html                  # إجمالي ألفا في اليوم = 1GB
    assert "5.50 GB" not in html              # الإجمالي الكامل لم يَعُد


# ── (6) الحالة الخالية بلا بيانات ──
def test_empty_state_no_data(app):
    c = app.test_client()           # بلا أي seed
    _auth(c)
    res = c.get(PATH)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # حالة خالية صريحة، لا أرقام وهمية.
    assert "لا استهلاك في النتائج" in html
    assert "0.00 GB" in html        # KPI الإجمالي = صفر
    assert "متصل الآن: 0" in html


def test_search_filters_rows(app):
    c = app.test_client()
    _seed(app)
    _auth(c)
    html = c.get(PATH + "?q=beta").get_data(as_text=True)
    assert "بيتا علي" in html
    assert "ألفا حسن" not in html
