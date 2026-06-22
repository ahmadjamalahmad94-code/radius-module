# -*- coding: utf-8 -*-
"""اختبارات تدقيق التجاوب (fix/responsive-layout-audit).

تتحقّق على مستوى المصدر/القالب + الرندر (بلا متصفّح):
  • ورقة الإصلاحات responsive_fixes.css موجودة ومربوطة أخيرًا في التخطيط.
  • قاعدة تثبيت قوائم الشريط العلوي داخل الشاشة على الجوّال موجودة.
  • قاعدة تمرير الجداول أفقيًّا (إلغاء overflow-x:hidden للـuds-table-wrap).
  • صفحات القوائم الرئيسية تُصيّر 200 وتحوي حاويات جداول قابلة للتمرير.
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(REPO, "app", "static", "css", "responsive_fixes.css")
LAYOUT = os.path.join(REPO, "app", "templates", "admin", "_admin_layout.html")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def app():
    import tempfile
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    return a


@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "preview"
        s["admin_name"] = "معاينة"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    return c


# ─────────────────────── ورقة الإصلاحات وربطها ───────────────────────

def test_responsive_fixes_css_exists():
    assert os.path.exists(CSS), "ورقة responsive_fixes.css مفقودة"


def test_layout_links_responsive_fixes_last():
    s = _read(LAYOUT)
    assert "responsive_fixes.css" in s, "الورقة غير مربوطة في التخطيط"
    # يجب أن تُحمَّل بعد طبقة توحيد الأسلوب لتفوز في الـcascade.
    assert s.index("style_unification.css") < s.index("responsive_fixes.css"), \
        "responsive_fixes.css يجب أن يُحمَّل بعد style_unification.css"


# ─────────────────────── الصنف 1: قوائم الشريط العلوي ───────────────────────

def test_topbar_menus_clamped_on_mobile():
    s = _read(CSS)
    # قاعدة جوّال تثبّت القوائم كـfixed داخل الشاشة.
    assert "#bell-menu" in s and "#notif-menu" in s
    assert "position: fixed !important" in s
    # تُحدَّد بحدّ أقصى للعرض/إلغاء min-width كي لا تخرج عن الحافة.
    assert "min-width: 0 !important" in s


# ─────────────────────── الصنف 2: تمرير الجداول ───────────────────────

def test_uds_table_wrap_scrolls_horizontally():
    s = _read(CSS)
    # لا بدّ من إعادة تفعيل التمرير الأفقي لحاوية uds-table-wrap (كانت hidden).
    m = re.search(r"\.uds-table-wrap[^{]*\{[^}]*overflow-x:\s*auto", s)
    assert m, "قاعدة تمرير uds-table-wrap الأفقي مفقودة"


def test_visible_scroll_affordance_present():
    s = _read(CSS)
    # مؤشّر تمرير مرئي كي يُدرك المستخدم أن الجدول قابل للسحب.
    assert "scrollbar-width: thin" in s
    assert "::-webkit-scrollbar" in s


def test_source_no_longer_only_clips_uds_tables():
    # ضمان أن قاعدة الإصلاح تُلغي القصّ الأصلي (overflow-x:hidden) في unified_design.
    base = os.path.join(REPO, "app", "static", "css", "unified_design.css")
    assert "overflow-x: hidden" in _read(base), "تغيّر سلوك الأصل — راجع الاختبار"
    fix = _read(CSS)
    assert "overflow-x: auto" in fix


# ─────────────────────── رندر الصفحات الرئيسية ───────────────────────

KEY_PAGES = [
    "/admin/radius/subscribers",
    "/admin/radius/cards/overview",
    "/admin/radius/plans",
    "/admin/radius/reports/sessions",
    "/admin/radius/network/devices",
    "/admin/radius/connected-stats",
    "/admin/radius/webhooks",
    "/admin/radius/tunnels",
    "/admin/radius/settings/system",
]


@pytest.mark.parametrize("path", KEY_PAGES)
def test_key_pages_render_200(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}"


@pytest.mark.parametrize("path", KEY_PAGES)
def test_key_pages_link_responsive_fixes(client, path):
    r = client.get(path)
    assert b"responsive_fixes.css" in r.data, f"{path}: الورقة غير محمّلة"


def test_list_pages_use_scrollable_table_container():
    # قوالب القوائم تستخدم حاوية الجدول القابلة للتمرير (مستقلّ عن البيانات).
    tpl_dir = os.path.join(REPO, "app", "templates", "radius")
    hits = 0
    for name in ("plans_list.html", "subscribers_list.html", "cards_overview.html",
                 "admins_list.html", "rep_sessions.html"):
        path = os.path.join(tpl_dir, name)
        if not os.path.exists(path):
            continue
        s = _read(path)
        if ("uds-table-wrap" in s) or ("hub-table-wrap" in s):
            hits += 1
    assert hits >= 3, f"حاويات الجداول القابلة للتمرير غير كافية ({hits})"
