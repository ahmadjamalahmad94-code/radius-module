# -*- coding: utf-8 -*-
"""المعرض الموحّد بتبويبات أنواع المنشآت (feat/designer-unified-gallery).

دَمج «معرض التصاميم» + «قوالب جاهزة حسب نوع منشأتك» في معرضٍ واحد بشريط
تبويبات 7 أقسام (بالترتيب الذي حدّده المالك)، قسمٌ واحد ظاهر في كل مرّة،
4–5 تصاميم لكل قسم. IA: ① التصاميم → ② الألوان والخطوط → ③ الإضافات.
"""
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, "app", "templates", "radius", "mt_login_designer.html")

EXPECTED_SECTIONS = ["general", "cafe", "cowork", "company", "education",
                     "restaurant", "retail"]
EXPECTED_LABELS = ["شبكة عامة", "كافي شوب", "مساحة عمل حر", "شركة",
                   "مؤسسة تعليمية", "مطعم", "متاجر وتسوّق"]


# ── وحدة: بناء الأقسام في الـroute ──

def test_route_sections_are_the_seven_in_order():
    from app.radius.routes import mt_login_designer as mod
    keys = [s[0] for s in mod._TEMPLATE_SECTIONS]
    labels = [s[1] for s in mod._TEMPLATE_SECTIONS]
    assert keys == EXPECTED_SECTIONS
    assert labels == EXPECTED_LABELS


def test_each_section_has_4_or_5_templates():
    from app.radius.routes import mt_login_designer as mod
    for key, label, icon, slugs in mod._TEMPLATE_SECTIONS:
        assert 4 <= len(slugs) <= 5, f"{key}: {len(slugs)} (يجب 4–5)"


def test_section_slugs_are_real_templates():
    from app.radius.routes import mt_login_designer as mod
    from app.radius.services import hotspot_templates as ht
    for key, label, icon, slugs in mod._TEMPLATE_SECTIONS:
        for s in slugs:
            assert s in ht.TEMPLATES_BY_SLUG, f"{key}: slug غير موجود «{s}»"


def test_build_sections_marks_active_and_appends_customs():
    from app.radius.routes import mt_login_designer as mod
    lib = [{"slug": "classic", "name_ar": "كلاسيك", "description_ar": "",
            "is_custom": False, "custom_id": 0},
           {"slug": "card", "name_ar": "بطاقة", "description_ar": "",
            "is_custom": False, "custom_id": 0},
           {"slug": "custom:5", "name_ar": "خاص", "description_ar": "",
            "is_custom": True, "custom_id": 5}]
    sections, active = mod._template_sections(lib, "classic")
    # classic ضمن «مؤسسة تعليمية» → القسم النشط.
    assert active == "education"
    by_key = {s["key"]: s for s in sections}
    assert by_key["education"]["templates"], "القسم النشط فارغ"
    # التصاميم الخاصّة تُلحَق بـ«شبكة عامة».
    gen_slugs = [t["slug"] for t in by_key["general"]["templates"]]
    assert "custom:5" in gen_slugs


# ── القالب: المعرض الموحّد + IA ──

@pytest.fixture(scope="module")
def page():
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
        adm = admins_repo.create_admin(username="ug_super", password="x",
                                       full_name="m", is_super_admin=True)
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                      "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                      ("rtr", "r1", "10.0.0.1", "s", "mikrotik", "other",
                       "2026-01-01"))
        aid = adm.id
    c = a.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_user"] = "ug_super"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    r = c.get("/admin/radius/mt/1/login-designer")
    assert r.status_code == 200
    return r.data.decode("utf-8", "replace")


def test_unified_gallery_tab_strip_and_panels(page):
    assert "data-mtld-gtabs" in page                 # شريط التبويبات
    assert page.count('data-mtld-gsec="') == 7       # 7 لوحات أقسام
    for label in EXPECTED_LABELS:
        assert label in page, f"تسمية القسم مفقودة: {label}"


def test_only_one_section_visible_others_hidden(page):
    import re
    # 6 لوحات تَحمل سمة hidden (واحدة فقط ظاهرة).
    hidden = len(re.findall(r'data-mtld-gsec="[^"]+"[^>]*\shidden', page))
    assert hidden == 6, f"المفروض 6 مخفيّة، وُجد {hidden}"


def test_old_two_galleries_removed(page):
    assert "قوالب جاهزة حسب نوع منشأتك" not in page
    assert "mtld-vthumb" not in page         # شبكة المعرض القديمة
    assert "mtld-vgrid" not in page
    assert "gallery_by_vertical" not in page


def test_tab_switch_js_and_selection_present(page):
    assert "data-mtld-gtab" in page          # أزرار التبويب
    assert "showGSection" in page             # سكربت التبديل
    assert "template_slug" in page            # آليّة اختيار التصميم
    assert page.count("data-mt-designer-template") >= 20


def test_addons_section_surfaced_not_buried(page):
    # ③ الإضافات ظاهرة بعنوان واضح (لا data-mtld-sec="addons" المخفيّة).
    assert "③ الإضافات" in page
    assert "mtld-addons-section" in page
    assert 'data-mtld-sec="addons"' not in page
    assert "data-mtld-addons" in page         # اللوح نفسه باقٍ يعمل


def test_ia_step_labels(page):
    assert "① اختر تصميمًا" in page
    assert "② تخصيص التصميم — الألوان والخطوط" in page
    assert "③ الإضافات — محتوى التصميم" in page
