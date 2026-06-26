# -*- coding: utf-8 -*-
"""تنظيم وتوضيح مصادر التصميم الثلاثة في مصمّم صفحة الدخول
(fix/designer-section-clarity):

  • الترتيب: حُزمة جاهزة ① → معرض التصاميم ② → الألوان والحقول ③، و«الأصول
    المستضافة» نُقلت أسفل مسار العمل (خُطوة متقدّمة).
  • نصّ مساعدة دقيق تحت كل عُنوان يُميّز الثلاثة (دون ادّعاء غير صحيح).
  • تلميح «الفرق؟» مُشترك يَربط الثلاثة.
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, "app", "templates", "radius", "mt_login_designer.html")


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def src():
    return _read(TPL)


# ── الترتيب على مستوى المصدر ──

def _sec(src, title):
    # موضع استدعاء hub.section الفعليّ (لا ذِكر العنوان في التعليقات).
    i = src.index('hub.section("' + title + '"')
    return i


def test_section_order_bundle_gallery_customize(src):
    b = _sec(src, 'قوالب جاهزة حسب نوع منشأتك')
    g = _sec(src, 'معرض التصاميم')
    c = _sec(src, 'تخصيص التصميم')
    assert b < g < c, "الترتيب يجب أن يكون: حُزمة ← معرض ← تخصيص"


def test_assets_section_moved_below_workflow(src):
    # قسم «الأصول المستضافة» صار بعد «تخصيص التصميم».
    c = _sec(src, 'تخصيص التصميم')
    a = _sec(src, 'الأصول المستضافة (فيديو/خط)')
    assert c < a, "قسم الأصول يجب أن يكون أسفل مسار العمل (بعد التخصيص)"
    # ولم يَعُد بين الحُزمة والمعرض.
    b = _sec(src, 'قوالب جاهزة حسب نوع منشأتك')
    g = _sec(src, 'معرض التصاميم')
    assert not (b < a < g), "قسم الأصول ما زال يَقطع تسلسل البداية"


# ── دقّة النصّ المُميِّز للثلاثة ──

def test_bundle_help_says_replaces_everything_incl_colors(src):
    assert 'نقطة البداية' in src
    assert 'يَستبدل التصميم بالكامل بما فيه' in src and 'الألوان' in src


def test_gallery_help_says_layout_only_keeps_colors(src):
    # يُغيّر التخطيط فقط ويُبقي الألوان (لا يَدّعي تغيير الألوان).
    assert 'بدّل التخطيط' in src
    assert 'ألوانك الحالية تبقى كما هي' in src


def test_colors_note_says_colors_only(src):
    assert 'mtld-colors-note' in src
    assert 'يُغيّران ألوان التصميم فقط' in src
    assert 'لا يبدّلان القالب' in src


# ── خُطوات ① ② ③ ──

def test_step_markers_present(src):
    assert '① ابدأ هنا' in src
    assert '② التخطيط' in src
    assert '③ الألوان فقط' in src


# ── تلميح «الفرق؟» المُشترك ──

def test_difference_hint_defined_once_and_reused(src):
    # يُعرَّف مرّة عبر {% set _diff_hint %} ويُستعمَل في الثلاثة.
    assert re.search(r'\{%\s*set\s+_diff_hint\s*%\}', src), "تعريف _diff_hint مفقود"
    assert src.count('{{ _diff_hint }}') >= 3, "تلميح «الفرق؟» لا يُغطّي الثلاثة"
    assert src.count('mtld-diff-hint') >= 1
    # نصّ التلميح يَذكر التمييز الدقيق للثلاثة.
    assert 'حُزمة كاملة تستبدل التصميم والألوان' in src
    assert 'يبدّل القالب/التخطيط فقط ويُبقي ألوانك' in src
    assert 'يُغيّر الألوان فقط دون تبديل القالب' in src


# ── سلامة الرندر ──

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
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        adm = admins_repo.create_admin(username="clr_super", password="x",
                                       full_name="m", is_super_admin=True)
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                      "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                      ("rtr", "r1", "10.0.0.1", "s", "mikrotik", "other",
                       "2026-01-01"))
        a._clr_admin_id = adm.id
    return a


def test_designer_page_renders_200_with_clarity(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = app._clr_admin_id
        s["admin_user"] = "clr_super"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    r = c.get("/admin/radius/mt/1/login-designer")
    assert r.status_code == 200
    h = r.data.decode("utf-8", "replace")
    # التلميح المُشترك صُيِّر خامًا (Markup) لا مُهرَّبًا.
    assert 'class="hub-hint mtld-diff-hint"' in h
    assert "&lt;span" not in h.split("mtld-diff-hint")[0][-200:]
