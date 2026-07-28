"""تحقّق أمني/انحدار: المدير الرئيسي (super_admin) له وصول كامل دائمًا.

بعد توسعة نظام الصلاحيات (مفاتيح دقيقة + وضع إخفاء/تجميد للأقسام غير
المصرّح بها) ظهر خطر أن يرى المديرُ الرئيسي الأقسامَ مقفلة/مخفية إن لم
يُعامَل كـ super_admin. هذه الاختبارات تثبت العقد الذي يجب ألا ينكسر:

  (أ) super_admin يجتاز كل حُرّاس المسارات (لا 403 على أي مسار محروس،
      بما فيها مسارات «__super__»: الأدوار/النسخ/المستأجرون).
  (ب) can(any_key) = True لكل مفتاح صلاحية معروف + «__super__».
  (ج) السايدبار يعرض كل الأقسام بلا تجميد/إخفاء حتى مع
      security.unauthorized_ui = "hide" (الحالة الأصعب).

ملاحظة: العقد كله مبني على علم الجلسة session["is_super_admin"], لا على
عدّ المفاتيح — لذا فقدان العلم في قاعدة الإنتاج (بيانات) هو السبب
المعتاد لظهور الأقسام مقفلة، لا الكود.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user="admin", pw="admin"):
    return client.post(
        "/admin/radius/login",
        data={"username": user, "password": pw},
        follow_redirects=False,
    )


# ─────────────────────────────────────────────────────────────
# (أ) يجتاز كل حُرّاس المسارات — لا 403 على المحروس، حتى «__super__»
# ─────────────────────────────────────────────────────────────
def test_super_admin_passes_every_route_guard(client):
    r = _login(client)
    assert r.status_code in {302, 303}, "دخول المدير الرئيسي يجب أن ينجح"

    # تأكيد أن الجلسة فعلًا معلّمة super_admin (جذر العقد كله)
    with client.session_transaction() as sess:
        assert sess.get("is_super_admin") is True

    from app.radius.routes.blueprint import _PERM_GUARDED

    # نطلب GET على كل endpoint محروس — المدير الرئيسي يجب ألا يُمنع (≠403)
    # أيًا كان مفتاحه (بما فيها مفاتيح «__super__»). نتسامح مع 200/302/4xx
    # غير 403 (بعض المسارات تتطلب معاملات أو تُعيد توجيهًا) — المهم لا منع.
    # نمرّ على url_map بدل url_for لتفادي المسارات ذات المعاملات الإلزامية.
    blocked = []
    seen = 0
    for rule in client.application.url_map.iter_rules():
        if not rule.endpoint.startswith("radius."):
            continue
        name = rule.endpoint.split(".", 1)[1]
        if name not in _PERM_GUARDED:
            continue
        if "GET" not in (rule.methods or set()):
            continue
        if any(arg for arg in rule.arguments):
            continue  # نتخطى ما يتطلب معاملات في المسار
        seen += 1
        res = client.get(rule.rule, follow_redirects=False)
        if res.status_code == 403:
            blocked.append((name, rule.rule))
    assert seen > 0, "يجب أن نكون قد فحصنا مسارات محروسة فعلًا"
    assert not blocked, f"المدير الرئيسي مُنِع (403) من مسارات محروسة: {blocked}"


def test_super_admin_not_blocked_on_guarded_write(client):
    _login(client)
    client.get("/admin/radius/")
    with client.session_transaction() as sess:
        tok = sess.get("_csrf_token")
    # كتابة محروسة بـ «__super__» فعليًا — يجب ألا تُمنع بـ 403
    res = client.post(
        "/admin/radius/finance/ledger/void",
        data={"entry_id": "999999", "reason": "qa", "_csrf_token": tok},
        follow_redirects=False,
    )
    assert res.status_code != 403


# ─────────────────────────────────────────────────────────────
# (ب) can(any_key) = True لكل مفتاح + «__super__»
# ─────────────────────────────────────────────────────────────
def test_can_returns_true_for_every_permission(app):
    from app.radius.auth.ui_permissions import can
    from app.radius.core.constants import ALL_PERMISSIONS

    with app.test_request_context("/admin/radius/"):
        from flask import session
        session["is_super_admin"] = True
        # المدير الرئيسي لا يحمل أي مفتاح في القائمة, ومع ذلك يمرّ بالكامل
        session["permissions"] = []
        for key in ALL_PERMISSIONS:
            assert can(key) is True, f"can({key!r}) يجب أن يكون True للمدير الرئيسي"
        # مفتاح «المدير الرئيسي فقط» يمرّ أيضًا
        assert can("__super__") is True
        # ومفتاح وهمي غير موجود يمرّ كذلك (العلم يتجاوز كل فحص)
        assert can("totally.unknown.key") is True


def test_can_blocks_super_only_key_for_non_super(app):
    """ضدّ-تحقّق: غير المدير الرئيسي يُمنع من «__super__» — حتى لا يكون
    الاختبار صحيحًا بالصدفة (can ترجع True دائمًا)."""
    from app.radius.auth.ui_permissions import can

    with app.test_request_context("/admin/radius/"):
        from flask import session
        session["is_super_admin"] = False
        session["permissions"] = ["users.view"]
        assert can("__super__") is False
        assert can("users.view") is True
        assert can("nas.delete") is False


# ─────────────────────────────────────────────────────────────
# (ج) السايدبار يعرض كل الأقسام بلا تجميد/إخفاء حتى في وضع hide
# ─────────────────────────────────────────────────────────────
# عناوين الأقسام الرئيسية الأحد عشر — يجب أن تظهر كلها للمدير الرئيسي
_SECTION_LABELS = [
    "المشتركون",
    "البطاقات",
    "البطاقات الإلكترونية",
    "العروض والسرعات",
    "الشبكة",
    "المال والتحصيل",
    "التشغيل والمخاطر",
    "التقارير",
    "الدعم",
    "الإدارة",
    "التكامل والجسر",
]


def test_sidebar_shows_all_sections_no_frozen_in_hide_mode(client, app):
    # نُفعّل أقسى وضع: إخفاء غير المصرّح به
    from app.radius.core.tenant import DEFAULT_TENANT_ID
    from app.radius.db.repos import tenants_repo
    from app.radius.auth.ui_permissions import UNAUTH_UI_SETTING_KEY

    with app.app_context():
        tenants_repo.set_setting(DEFAULT_TENANT_ID, UNAUTH_UI_SETTING_KEY, "hide")

    _login(client)
    res = client.get("/admin/radius/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # كل الأقسام حاضرة
    for label in _SECTION_LABELS:
        assert label in html, f"القسم «{label}» مفقود من سايدبار المدير الرئيسي"

    # لا أي بند/قسم مجمّد أو تلميح «ليست ضمن صلاحياتك» للمدير الرئيسي
    assert "hb-side-frozen" not in html, "ظهر تجميد في سايدبار المدير الرئيسي"
    assert "ليست ضمن صلاحياتك" not in html, "ظهر تلميح منع في سايدبار المدير الرئيسي"
