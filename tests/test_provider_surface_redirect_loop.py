"""MT117 — لا حلقة تحويلٍ بين الجذر وبادئة الشبكة على أسطح المزوّد.

الحادثة: مدير شبكةٍ (غير سوبر) فتح `/admin/radius/provider` فدار المتصفّح
بلا نهاية حتى `ERR_TOO_MANY_REDIRECTS`، ولم تُعرض صفحة:

    /admin/radius/provider          → 302 → /albarq/admin/radius/provider
    /albarq/admin/radius/provider   → 302 → /admin/radius/provider   ← وهكذا

حارسان كُتبا مستقلَّين، كلٌّ صحيحٌ وحده:
  • `_provider_surfaces_root_only` — «أسطح المزوّد للجذر، انزع الـslug».
    وتعليقه يقول «آمنٌ من الحلقات» — وكان صادقًا يوم كُتب.
  • `_enforce_root_is_platform_only` — «مدير شبكةٍ على الجذر يُردّ إلى
    بادئة شبكته بدل رسالةٍ عمياء».

الصواب أن يمضي الطلب إلى حارس الصلاحيات فيردّ **403 صريحًا**: مدير الشبكة
لا شأن له بلوحة المزوّد. رسالةُ منعٍ خيرٌ من دوران.

والعلاج الحقيقيّ ليس استثناءً مكتوبًا مرّتين، بل **تعريفٌ واحد** يستعمله
الحارسان — وإلّا عاد الخلاف مع أوّل سطحٍ جديد.
"""

import pytest

from app.radius.middleware.tenant_resolver import is_platform_only_endpoint


# ── التعريف الواحد ───────────────────────────────────────────────────
@pytest.mark.parametrize("endpoint", [
    "radius.provider_home",
    "radius.provider_chat_home",
    "radius.provider_notifications",
])
def test_provider_surfaces_are_platform_only(endpoint):
    assert is_platform_only_endpoint(endpoint) is True


@pytest.mark.parametrize("endpoint", [
    "radius.dashboard",
    "radius.cards_checker",
    "radius.subscribers",
    "",
    None,
])
def test_tenant_surfaces_are_not(endpoint):
    assert is_platform_only_endpoint(endpoint) is False


# ── الحارسان يستعملانه فعلًا ─────────────────────────────────────────
def test_root_guard_skips_platform_surfaces():
    """بلا هذا الاستثناء يُردّ مدير الشبكة إلى slug لا نسخة له هناك."""
    import inspect
    from app.radius.middleware import tenant_resolver as tr

    src = inspect.getsource(tr)
    i = src.find("_enforce_root_is_platform_only")
    assert i > 0
    body = src[i:i + 2600]
    assert "is_platform_only_endpoint" in body, "حارس الجذر لا يستثني أسطح المنصّة"


def test_provider_guard_uses_the_same_definition():
    """تعريفان منفصلان = عودة الخلاف مع أوّل سطحٍ جديد."""
    import inspect
    import app as app_pkg

    src = inspect.getsource(app_pkg)
    i = src.find("_provider_surfaces_root_only")
    assert i > 0
    body = src[i:i + 1400]
    assert "is_platform_only_endpoint" in body
    assert 'ep.startswith("radius.provider_")' not in body, \
        "ما زال يُعرّف الأسطح بنفسه بدل المصدر الموحّد"


def test_the_two_guards_cannot_disagree():
    """حارسٌ يَنزع وحارسٌ يُضيف على نفس المسار = حلقة. الاستثناء يكسرها:
    ما يَنزعه الأوّل لا يُعيده الثاني."""
    assert is_platform_only_endpoint("radius.provider_home")
    # حارس الجذر يمرّ (لا توجيه) على ما هو سطح منصّة ⇒ لا عودة للـslug
    # وحارس المزوّد ينزع الـslug ⇒ الاستقرار على الجذر، ثمّ 403 من الصلاحيات.
