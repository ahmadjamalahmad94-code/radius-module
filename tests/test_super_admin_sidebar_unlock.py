"""
انحدار: المدير الرئيسي يجب أن يرى كل أقسام السايدبار دائمًا — لا تجميد.

السياق (إنتاج، fix/super-admin-sidebar-unlock): مدير رئيسي محلي
(is_super_admin=1) كان يرى كل الأقسام مجمّدة بقفل بينما الصفحات تفتح
عبر الرابط مباشرة — أي العطل في رسم السايدبار لا في حُرّاس المسارات.

هذه الاختبارات ترندر `admin/_sidebar.html` فعليًا عبر تطبيق Flask حقيقي
(create_app + test_request_context + render_template):

  • سوبر أدمن + وضع تجميد (freeze)        → صفر `hb-side-frozen`، كل الأقسام ظاهرة.
  • سوبر أدمن لكن can() يفشل بصمت دائمًا   → ما زال صفر `hb-side-frozen` (fail-open حقيقي).
  • غير-سوبر بصلاحية واحدة + وضع تجميد     → أقسام غير مصرّح بها مجمّدة (السلوك الصحيح يبقى).
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "super_sidebar.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _render_sidebar(app, *, session_overrides: dict):
    """يرندر السايدبار ضمن سياق طلب حقيقي بجلسة مُهيّأة."""
    from flask import render_template, session

    # نضمن أن إعداد المستأجر الافتراضي = تجميد (freeze) — وهو الافتراضي أصلًا،
    # لكن نثبّته صراحةً حتى لا يعتمد الاختبار على قيمة ضمنية. نستخدم المفتاح
    # الحرفي بدل استيراده من ui_permissions حتى يعمل المساعد حتى عندما يحاكي
    # اختبارٌ غيابَ الطبقة (تصفير وحدتها في sys.modules).
    from app.radius.core.tenant import DEFAULT_TENANT_ID
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(DEFAULT_TENANT_ID, "security.unauthorized_ui", "freeze")

    with app.test_request_context("/admin/radius/dashboard"):
        for k, v in session_overrides.items():
            session[k] = v
        return render_template("admin/_sidebar.html")


def test_super_admin_sees_every_section_no_freeze(app):
    """سوبر أدمن + وضع تجميد → لا توجد ولا صف مجمّد، والأقسام ظاهرة كروابط."""
    html = _render_sidebar(app, session_overrides={
        "admin_id": 1,
        "is_super_admin": True,
        "tenant_id": 1,
        "permissions": [],          # حتى بلا أي صلاحية مفصّلة، السوبر يرى الكل
    })
    assert "hb-side-frozen" not in html, "السوبر أدمن يجب ألا يرى أي بند مجمّد"
    # الأقسام الرئيسية ظاهرة (رؤوس أقسام فعلية لا رؤوس مجمّدة)
    assert "hb-side-section-head" in html
    for label in ("المشتركون", "البطاقات", "العروض والسرعات",
                  "الشبكة", "المال والتحصيل", "التقارير"):
        assert label in html, f"قسم «{label}» يجب أن يظهر للسوبر أدمن"
    # روابط فعلية (لا أزرار مجمّدة) — على الأقل بنود فرعية قابلة للنقر موجودة
    assert "hb-side-subitem" in html


def test_super_admin_unlocked_even_if_can_fails_silently(app, monkeypatch):
    """الحالة الحرجة (#3): can() يُرجِع False لكل مفتاح (طبقة RBAC فشلت بصمت).

    قبل الإصلاح: _rbac_ui=True + can()→False ⇒ كل الأقسام تتجمّد حتى للسوبر.
    بعد الإصلاح: السايدبار يقرأ علم السوبر من الجلسة ويتجاوز can() ⇒ لا تجميد.
    """
    import app.radius.auth.ui_permissions as ui_perms
    # نحاكي «الفشل الصامت»: can() دائمًا False مهما كان المفتاح.
    monkeypatch.setattr(ui_perms, "can", lambda *_a, **_k: False)

    html = _render_sidebar(app, session_overrides={
        "admin_id": 1,
        "is_super_admin": True,
        "tenant_id": 1,
        "permissions": [],
    })
    assert "hb-side-frozen" not in html, (
        "حتى مع فشل can() الصامت، السوبر أدمن يجب أن يرى كل الأقسام (fail-open)"
    )
    assert "المشتركون" in html and "الشبكة" in html


def test_layer_missing_fails_open_for_everyone(app, monkeypatch):
    """طبقة الصلاحيات غائبة (محاكاة بناء إنتاج ناقص الملف) → عرض الكل لغير-السوبر.

    قبل الإصلاح: الأغلفة محقونة دائمًا وتبتلع ImportError وتُرجِع False ⇒
    _rbac_ui=True + كل can()=False ⇒ تجميد كامل. بعد الإصلاح: الحقن مشروط
    باستيراد الطبقة؛ فشل الاستيراد ⇒ can/perm_for_endpoint غير محقونتين ⇒
    _rbac_ui=False ⇒ fail-open (لا تجميد) حتى لغير-السوبر.
    """
    import sys

    import app.radius.auth as _auth_pkg
    # نجعل `from app.radius.auth import ui_permissions` يفشل بـImportError:
    # يلزم تصفير مدخل sys.modules **و** إزالة السمة المخبّأة على الحزمة الأب
    # (وإلا أعادها from-import مباشرةً من كائن الحزمة متجاوزًا sys.modules).
    monkeypatch.delattr(_auth_pkg, "ui_permissions", raising=False)
    monkeypatch.setitem(sys.modules, "app.radius.auth.ui_permissions", None)

    html = _render_sidebar(app, session_overrides={
        "admin_id": 3,
        "is_super_admin": False,         # حتى غير-السوبر يُعرض له الكل عند غياب الطبقة
        "tenant_id": 1,
        "permissions": [],
    })
    assert "hb-side-frozen" not in html, (
        "عند غياب طبقة الصلاحيات يجب أن يرتدّ السايدبار لعرض الكل لا التجميد"
    )
    assert "المشتركون" in html and "الشبكة" in html


def test_non_super_limited_perms_freezes_unauthorized(app):
    """غير-سوبر بصلاحية users.view فقط + وضع تجميد → الأقسام الأخرى مجمّدة.

    يؤكّد أن السلوك الصحيح لغير-السوبر لم ينكسر بإصلاح fail-open للسوبر.
    """
    html = _render_sidebar(app, session_overrides={
        "admin_id": 2,
        "is_super_admin": False,
        "tenant_id": 1,
        "permissions": ["users.view"],   # المشتركون فقط مصرّح به
    })
    # قسم خارج الصلاحية يجب أن يُجمّد (مثلًا «العروض والسرعات» = plans.view)
    assert "hb-side-frozen" in html, "غير-السوبر يجب أن يرى بنودًا مجمّدة"
    # تلميح التجميد حاضر
    assert "ليست ضمن صلاحياتك" in html
