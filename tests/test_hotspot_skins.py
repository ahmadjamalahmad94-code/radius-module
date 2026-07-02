# -*- coding: utf-8 -*-
"""قوالب «الجلود» الجديدة: التسجيل في المكتبة، صحّة placeholders
المايكروتيك، وحارس عدم تسريب أي placeholder خام (المشكلة المُبلَّغة:
$(limit-bytes-remaining) ظهر خامًا في مرجع). شغّل الملف وحده."""
from __future__ import annotations

import re

import pytest

from app.radius.services import hotspot_skins as sk
from app.radius.services import hotspot_templates as ht

# placeholders راوتر أو إس المسموح ببقائها في login.html المنشورة
# (يملؤها الراوتر وقت الطلب). أي $(...) خارجها = تسريب يجب أن يفشل.
_ALLOWED_DOLLAR = set(ht.ROUTEROS_REQUIRED) | {
    "$(link-orig)", "$(link-orig-esc)", "$(username)", "$(mac-esc)",
    "$(if error)", "$(endif)",
}


def _defaults() -> dict:
    return {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}


def test_all_skins_registered():
    # food_cobrand رُقّي من جِلد بسيط إلى قالب شِلّ فاخر بعمودين (يُسجَّل في
    # LIBRARY مباشرةً عبر hotspot_template_food_cobrand) فنقص عدد الجلود من 10.
    assert len(sk.SKIN_SLUGS) == 9
    for slug in sk.SKIN_SLUGS:
        assert slug in ht.TEMPLATES_BY_SLUG, f"جلد غير مسجّل: {slug}"
        assert ht.TEMPLATES_BY_SLUG[slug] in ht.LIBRARY


@pytest.mark.parametrize("slug", sk.SKIN_SLUGS)
def test_skin_has_required_routeros_placeholders(slug):
    html = ht.TEMPLATES_BY_SLUG[slug].html
    assert ht.validate_routeros_placeholders(html) == [], \
        f"{slug}: placeholder مايكروتيك إجباري ناقص"


@pytest.mark.parametrize("slug", sk.SKIN_SLUGS)
def test_render_has_no_unresolved_our_placeholders(slug):
    """بعد render: لا يبقى أي {{متغيّر}} خام."""
    out = ht.render(slug, _defaults())
    leaks = re.findall(r"{{[^}]+}}", out)
    assert leaks == [], f"{slug}: متغيّرات لم تُستبدَل: {leaks}"


@pytest.mark.parametrize("slug", sk.SKIN_SLUGS)
def test_render_has_no_unknown_dollar_placeholder(slug):
    """حارس البَق: لا $(...) خارج القائمة المسموحة (لا تسريب مثل
    $(limit-bytes-remaining))."""
    out = ht.render(slug, _defaults())
    found = set(re.findall(r"\$\([^)]*\)", out))
    unknown = found - _ALLOWED_DOLLAR
    assert not unknown, f"{slug}: placeholders خام مسرّبة: {unknown}"


@pytest.mark.parametrize("slug", sk.SKIN_SLUGS)
def test_preview_strips_all_placeholders(slug):
    """معاينة المصمّم/المعرض: صفر $( وصفر {{ (تُجرَّد للعرض)."""
    out = ht.preview(slug, _defaults())
    assert "$(" not in out, f"{slug}: المعاينة تسرّب $("
    assert "{{" not in out, f"{slug}: المعاينة تسرّب {{{{"


@pytest.mark.parametrize("slug", sk.SKIN_SLUGS)
def test_render_is_complete_html(slug):
    out = ht.render(slug, _defaults())
    assert out.lstrip().startswith("<!DOCTYPE html>")
    assert "</body>" in out and "</html>" in out
    assert 'dir="rtl"' in out
    # نموذج الدخول الفعلي موجود
    assert 'name="login"' in out and 'name="username"' in out


def test_skins_render_with_custom_colors_and_photo():
    """الجلود مدفوعة بالرموز: ألوان وصورة من الإدخال تنعكس في الناتج."""
    vals = _defaults()
    vals.update({"ACCENT_COLOR": "#123456", "ACCENT2_COLOR": "#abcdef",
                 "BG_PHOTO_URL": "https://cdn.example.com/p.jpg",
                 "TENANT_NAME": "شبكة الاختبار"})
    out = ht.render("photo_backdrop", vals)
    assert "https://cdn.example.com/p.jpg" in out
    assert "شبكة الاختبار" in out
    out2 = ht.render("crimson_luxe", vals)
    assert "#abcdef" in out2  # اللون الثانوي مُطبَّق
