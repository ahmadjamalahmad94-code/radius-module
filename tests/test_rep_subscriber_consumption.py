# -*- coding: utf-8 -*-
"""تقرير الاستهلاك (مع فلتر النوع): التصنيف (مشترك/بطاقة/برودباند/هوت
سبوت/أخرى)، إعادة تحجيم كل تبويب لـKPI+الجدول، عمود/شارة النوع، اسم
الحزمة للبطاقات، التصدير يحترم الفلتر، العنوان/السايدبار، والحالة الخالية.
شغّل الملف وحده."""
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
        s.update(admin_id=1, admin_user="usage_admin", admin_name="Usage Admin",
                 is_super_admin=True, tenant_id=1, _csrf_token="usage-csrf")


def _seed_mixed(app, *, ahmad_active=True):
    """خمسة أنواع بقيم معروفة (GB):
      بطاقة  7772  (حزمة «حزمة الساعة»، بلا اسم) → تنزيل2 رفع1 = 3
      هوت سبوت ahmad (أحمد حسن)                  → تنزيل5 رفع2 = 7  (الأعلى)
      برودباند ppp1 (شركة الاتصال، PPPoE)        → تنزيل4 رفع1 = 5
      هوت سبوت sara  (سارة)                       → تنزيل1 رفع0.5= 1.5
      أخرى    9999  (غير مرتبط)                   → تنزيل0.5 رفع0= 0.5
    إجمالي الكل = 17GB، مشتركين = 13.5GB، بطاقات = 3GB، برودباند = 5GB،
    هوت سبوت = 8.5GB، أخرى = 0.5GB."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(1,1,?,?,?)", ("باقة الساعة", "Hotspot", "2026-01-01"))
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(2,1,?,?,?)", ("باقة الألياف", "PPPoE", "2026-01-01"))
            c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,created_at) "
                      "VALUES(1,'B1',?,1,'2026-01-01')", ("حزمة الساعة",))
            c.execute("INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,created_at) "
                      "VALUES(1,1,'7772','pw',1,'2026-01-01')")
            c.execute("INSERT INTO subscribers(tenant_id,username,full_name,mobile,plan_id,"
                      "service_type,status,created_at) "
                      "VALUES(1,'ahmad',?,?,1,'Hotspot','enabled','2026-01-01')",
                      ("أحمد حسن", "0590000001"))
            c.execute("INSERT INTO subscribers(tenant_id,username,full_name,mobile,plan_id,"
                      "service_type,status,created_at) "
                      "VALUES(1,'ppp1',?,?,2,'PPPoE','enabled','2026-01-01')",
                      ("شركة الاتصال", "0590000002"))
            c.execute("INSERT INTO subscribers(tenant_id,username,full_name,mobile,plan_id,"
                      "service_type,status,created_at) "
                      "VALUES(1,'sara',?,?,1,'Hotspot','enabled','2026-01-01')",
                      ("سارة علي", "0590000003"))

            def acct(user, din, dout, active=False):
                c.execute(
                    "INSERT INTO radacct(tenant_id,acctsessionid,username,nasipaddress,"
                    "acctstarttime,acctstoptime,acctsessiontime,acctinputoctets,"
                    "acctoutputoctets) VALUES(1,?,?,?,?,?,?,?,?)",
                    (user + "-s", user, "10.0.0.1", "2026-06-10 09:00:00",
                     None if active else "2026-06-10 10:00:00", 3600,
                     int(din * GB), int(dout * GB)))
            acct("7772", 2, 1)
            acct("ahmad", 5, 2, active=ahmad_active)
            acct("ppp1", 4, 1)
            acct("sara", 1, 0.5)
            acct("9999", 0.5, 0)  # غير مرتبط → أخرى


def _html(app, query=""):
    # نزرع مرّة واحدة لكل app (قد يُستدعى _html عدّة مرّات في اختبار واحد).
    if not getattr(app, "_usage_seeded", False):
        _seed_mixed(app)
        app._usage_seeded = True
    c = app.test_client()
    _auth(c)
    return c.get(PATH + query).get_data(as_text=True)


# ── (1) العنوان + التبويبات + عمود النوع ──
def test_title_renamed_and_tabs_present(app):
    html = _html(app)
    assert "تقرير الاستهلاك" in html
    assert "استهلاك المشتركين" not in html  # العنوان القديم اختفى
    assert 'data-testid="usage-type-tabs"' in html
    for tab in ("الكل", "مشتركين", "بطاقات", "برودباند", "هوت سبوت", "أخرى"):
        assert tab in html, f"تبويب مفقود: {tab}"
    assert "النوع" in html  # عمود الجدول


# ── (2) التصنيف: كل تبويب يحصر السجلّات الصحيحة ──
@pytest.mark.parametrize("utype,present,absent", [
    ("all", ["7772", "ahmad", "ppp1", "sara", "9999"], []),
    ("card", ["7772"], ["ahmad", "ppp1", "sara", "9999"]),
    ("subscriber", ["ahmad", "ppp1", "sara"], ["7772", "9999"]),
    ("broadband", ["ppp1"], ["7772", "ahmad", "sara", "9999"]),
    ("hotspot", ["ahmad", "sara"], ["7772", "ppp1", "9999"]),
    ("other", ["9999"], ["7772", "ahmad", "ppp1", "sara"]),
])
def test_filter_rescopes_table(app, utype, present, absent):
    html = _html(app, "?type=" + utype)
    body = html.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    for u in present:
        assert f"{u}</bdi>" in body, f"{utype}: {u} مفقود"
    for u in absent:
        assert f"{u}</bdi>" not in body, f"{utype}: {u} ظهر خطأً"


# ── (3) إعادة تحجيم KPI لكل نوع (الإجمالي) ──
@pytest.mark.parametrize("utype,total_gb", [
    ("all", "17.00 GB"),
    ("card", "3.00 GB"),
    ("subscriber", "13.50 GB"),
    ("broadband", "5.00 GB"),
    ("hotspot", "8.50 GB"),
    ("other", "0.50 GB"),
])
def test_filter_rescopes_kpi_total(app, utype, total_gb):
    assert total_gb in _html(app, "?type=" + utype), f"{utype}: إجمالي KPI متوقّع {total_gb}"


# ── (4) شارة النوع في الجدول ──
def test_type_badge_labels(app):
    assert "بطاقة" in _html(app, "?type=card")
    assert "برودباند" in _html(app, "?type=broadband")
    assert "هوت سبوت" in _html(app, "?type=hotspot")
    assert "usage-badge" in _html(app)  # الشارة الملوّنة حاضرة


# ── (5) اسم الحزمة بديلًا لاسم البطاقة الفارغ ──
def test_card_name_fallback_shows_package(app):
    assert "حزمة الساعة" in _html(app, "?type=card")


# ── (6) أعلى مستهلك + الترتيب ──
def test_top_consumer_all(app):
    html = _html(app)
    assert "أحمد حسن" in html
    body = html.split("<tbody>", 1)[-1]
    assert body.index("ahmad") < body.index("ppp1")  # 7GB > 5GB


# ── (7) التصدير يحترم الفلتر + يضمّ عمود النوع ──
def test_export_respects_filter_rows(app):
    body = _html(app, "?type=card").split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    assert "7772" in body and "ahmad" not in body


def test_export_endpoint_includes_type_column(app):
    c = app.test_client()
    _auth(c)
    payload = {
        "_csrf_token": "usage-csrf", "title": "تقرير الاستهلاك", "fmt": "csv",
        "columns": json.dumps(["المستخدم", "النوع", "الإجمالي"]),
        "rows": json.dumps([["7772", "بطاقة", "3.00 GB"]]),
    }
    res = c.post("/admin/radius/export/table", data=payload)
    assert res.status_code == 200
    assert ".csv" in res.headers.get("Content-Disposition", "")
    body = res.get_data(as_text=True)
    assert "النوع" in body and "بطاقة" in body and "7772" in body


# ── (8) السايدبار + الصلاحية ──
def test_sidebar_label_and_path(app):
    html = _html(app)
    assert PATH in html
    assert "تقرير الاستهلاك" in html
    assert "المشتركون" in html


def test_perm_guard_wired():
    from app.radius.routes import blueprint as bp
    assert bp._PERM_GUARDED.get("rep_subscriber_consumption") == "reports.view"


# ── (9) المتصل الآن مفلتر بالنوع ──
def test_online_now_filtered(app):
    assert "متصل الآن: 1" in _html(app)            # ahmad نشط (الكل)
    assert "متصل الآن: 0" in _html(app, "?type=card")  # لا بطاقة نشطة


# ── (10) الحالة الخالية ──
def test_empty_state_no_data(app):
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert "لا استهلاك في النتائج" in html
    assert "0.00 GB" in html
    assert "متصل الآن: 0" in html


def test_empty_state_out_of_range(app):
    c = app.test_client()
    _seed_mixed(app)
    _auth(c)
    html = c.get(PATH + "?date_from=2030-01-01&date_to=2030-01-02").get_data(as_text=True)
    assert "لا استهلاك في النتائج" in html


# ── (11) البحث يشمل اسم الحزمة ──
def test_search_includes_package_name(app):
    body = _html(app, "?q=حزمة").split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    assert "7772" in body
    assert "ahmad" not in body


# ══════════ مقياس المقارنة (تنزيل/رفع/إجمالي) ══════════
import re  # noqa: E402


def _seed_metric(app):
    """حسابان: dlh عالي التنزيل (10↓/1↑=11)، ulh عالي الرفع (1↓/9↑=10).
    بالإجمالي والتنزيل: dlh أعلى؛ بالرفع: ulh أعلى — يثبت تبدّل الترتيب.
    RFC 2866: acctinputoctets = رفع (↑)، acctoutputoctets = تنزيل (↓)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                      "VALUES(1,1,'h','Hotspot','2026-01-01')")
            for u, fn in [("dlh", "عالي التنزيل"), ("ulh", "عالي الرفع")]:
                c.execute("INSERT INTO subscribers(tenant_id,username,full_name,plan_id,"
                          "service_type,status,created_at) "
                          "VALUES(1,?,?,1,'Hotspot','enabled','2026-01-01')", (u, fn))
            # dlh عالي التنزيل: acctoutputoctets(تنزيل)=10، acctinputoctets(رفع)=1
            c.execute("INSERT INTO radacct(tenant_id,acctsessionid,username,acctstarttime,"
                      "acctinputoctets,acctoutputoctets) VALUES(1,'a','dlh','2026-06-10 09:00:00',?,?)",
                      (int(1 * GB), int(10 * GB)))
            # ulh عالي الرفع: acctinputoctets(رفع)=9، acctoutputoctets(تنزيل)=1
            c.execute("INSERT INTO radacct(tenant_id,acctsessionid,username,acctstarttime,"
                      "acctinputoctets,acctoutputoctets) VALUES(1,'b','ulh','2026-06-10 09:00:00',?,?)",
                      (int(9 * GB), int(1 * GB)))


@pytest.mark.parametrize("metric,top", [("total", "dlh"), ("dl", "dlh"), ("ul", "ulh")])
def test_metric_changes_ranking(app, metric, top):
    c = app.test_client()
    _seed_metric(app)
    _auth(c)
    body = c.get(PATH + "?metric=" + metric).get_data(as_text=True).split("<tbody>", 1)[-1]
    first = re.search(r"<strong><bdi>(\w+)", body).group(1)
    assert first == top, f"metric={metric}: المتوقّع {top} في الصدارة"


def test_metric_default_is_total(app):
    # بلا metric → الإجمالي: dlh (11) قبل ulh (10).
    c = app.test_client()
    _seed_metric(app)
    _auth(c)
    body = c.get(PATH).get_data(as_text=True).split("<tbody>", 1)[-1]
    assert re.search(r"<strong><bdi>(\w+)", body).group(1) == "dlh"


def test_metric_label_in_kpi_and_chart(app):
    html = _html(app, "?metric=ul")
    assert "الأعلى استهلاكًا" in html        # عنوان KPI ثابت ومختصر
    assert "حسب" in html and "رفع" in html  # المقياس يظهر في سطر KPI الفرعي + عنوان أعلى-10


def test_metric_combines_with_type(app):
    # المقياس + النوع معًا: بطاقات فقط، بالرفع.
    body = _html(app, "?type=card&metric=ul").split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    assert "7772" in body and "ahmad" not in body


# ══════════ قوالب التاريخ ══════════
import datetime as _dt  # noqa: E402


@pytest.mark.parametrize("args,expected", [
    ({"preset": "today"}, ("2026-06-17", "2026-06-17", "today")),
    ({"preset": "yesterday"}, ("2026-06-16", "2026-06-16", "yesterday")),
    ({"preset": "week"}, ("2026-06-15", "2026-06-21", "week")),      # إثنين..أحد
    ({"preset": "month"}, ("2026-06-01", "2026-06-30", "month")),
    ({"spec_day": "2026-03-05"}, ("2026-03-05", "2026-03-05", "day")),
    ({"spec_week": "2026-W10"}, ("2026-03-02", "2026-03-08", "weekof")),
    ({"spec_month": "2026-02"}, ("2026-02-01", "2026-02-28", "monthof")),
    ({"date_from": "2026-01-01", "date_to": "2026-01-31"},
     ("2026-01-01", "2026-01-31", "custom")),
    ({}, ("", "", "custom")),
])
def test_date_range_resolver(args, expected):
    from app.radius.routes.reports import _resolve_usage_range
    assert _resolve_usage_range(args, _dt.date(2026, 6, 17)) == expected


def test_date_preset_priority_quick_over_spec(app):
    # القالب السريع يسبق المنتقي المحدّد (preset=today يفوز على spec_month).
    from app.radius.routes.reports import _resolve_usage_range
    df, dt, p = _resolve_usage_range(
        {"preset": "month", "spec_day": "2020-01-01"}, _dt.date(2026, 6, 17))
    assert (df, dt, p) == ("2026-06-01", "2026-06-30", "month")


def test_spec_month_scopes_end_to_end(app):
    # شهر محدّد يُعيد التحجيم: يونيو فيه بيانات (2026-06-10)، مايو فارغ.
    assert "أحمد حسن" in _html(app, "?spec_month=2026-06")
    assert "لا استهلاك في النتائج" in _html(app, "?spec_month=2026-05")


# ══════════ حضور الضوابط في الصفحة ══════════
def test_controls_present(app):
    html = _html(app)
    assert 'data-testid="usage-date-presets"' in html
    assert 'data-testid="usage-metric"' in html
    for p in ("اليوم", "أمس", "هذا الأسبوع", "هذا الشهر", "مخصّص"):
        assert p in html, f"قالب فترة مفقود: {p}"
    for nm in ('name="spec_day"', 'name="spec_week"', 'name="spec_month"'):
        assert nm in html, f"منتقٍ مفقود: {nm}"
    # أزرار المقياس (تنزيل/رفع/الإجمالي) حاضرة كروابط.
    assert 'data-usage-metric="dl"' in html and 'data-usage-metric="ul"' in html
    assert 'data-usage-metric="total"' in html


def test_single_date_control_set(app):
    """إصلاح التكرار: مجموعة واحدة فقط من منتقيات التاريخ على الصفحة —
    لا «اختر التاريخ» مكرّر في الحزام العلوي مع قسم «الفترة»."""
    html = _html(app)
    # كل حقل تاريخ يظهر مرّة واحدة بالضبط (لا نسخة علوية + نسخة في الفترة).
    for nm in ("date_from", "date_to", "spec_day", "spec_week", "spec_month"):
        assert html.count('name="%s"' % nm) == 1, \
            "حقل التاريخ %s مكرّر/مفقود (المتوقّع مرّة واحدة)" % nm
    # قسم «الفترة» الوحيد، ولا نموذج منتقيات مستقل قديم (دُمج في الحزام).
    assert html.count('data-testid="usage-date-presets"') == 1
    assert 'class="usage-spec" method' not in html  # لا <form> منتقيات يتيم
    # الحزام العلوي يبقى فيه البحث + الحجم + تطبيق + طباعة.
    assert 'name="q"' in html and 'name="limit"' in html


def test_date_filter_still_applies_after_dedup(app):
    """المصدر الوحيد يُرسل المدى صحيحًا: مخصّص + محدّد يُعيدان التحجيم."""
    # عيّنة data في 2026-06-10 (ضمن المثبّت _seed_mixed).
    assert "أحمد حسن" in _html(app, "?date_from=2026-06-01&date_to=2026-06-30")
    assert "لا استهلاك في النتائج" in _html(app, "?date_from=2030-01-01&date_to=2030-01-31")
    assert "أحمد حسن" in _html(app, "?spec_month=2026-06")
