# -*- coding: utf-8 -*-
"""اختبارات جودة مصمّم صفحة الدخول (fix/hotspot-designer-quality):

  A) زرّ رفع التصميم الخاصّ (منطقة السحب/النقر) موصول بجافاسكربت.
  C1) ثيمات الخَلفيّة الداكنة/المُشبَعة تَفرض نصًّا قَرائيًّا (تباين).
  C2) أجزاء «قبل الدخول» تُلَفّ في حاوية واحدة (احتواء/تكديس بلا تجاوز).
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGNER_TPL = os.path.join(REPO, "app", "templates", "radius",
                            "mt_login_designer.html")


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# ─────────────────── A) زرّ الرفع موصول ───────────────────

def test_custom_upload_dropzone_is_wired():
    s = _read(DESIGNER_TPL)
    # عناصر المنطقة موجودة.
    assert "data-mtld-custom-drop" in s
    assert "data-mtld-custom-file" in s
    # الجافاسكربت يَربط النقر بفتح المنتقي (كان مفقودًا → الزرّ ميّت).
    assert "openPicker" in s, "دالة فتح المنتقي مفقودة"
    assert re.search(r'querySelector\("\[data-mtld-custom-drop\]"\)', s), \
        "لا ربط لمنطقة السحب في الجافاسكربت"
    assert "input.click()" in s, "لا استدعاء لفتح منتقي الملفات"
    # دعم السحب والإفلات + لوحة المفاتيح.
    assert '"drop"' in s and "dataTransfer" in s
    assert '"keydown"' in s


# ─────────────────── C1) تباين نصّ الثيمات ───────────────────

def _theme_frag(key, accent="#16a34a", **cfg):
    from app.radius.services import hotspot_addons as ad
    import app.radius.services.hotspot_addons_themes  # noqa: F401 — register
    cfgmap = {key: {"enabled": True, "config": cfg}}
    norm = ad.normalize_config(cfgmap)
    return ad.render_prelogin_fragments(norm, {"accent": accent})


def test_seasonal_theme_forces_readable_heading():
    html = _theme_frag("theme_seasonal", season="ramadan")
    # العُنوان أبيض (يَتجاوز h1 الأخضر للقالب) + الوصف فاتح.
    assert "h1,h2,h3,.card h1" in html
    assert "color:#fff!important" in html
    assert "rgba(255,255,255,.92)!important" in html  # الوصف/التسميات
    assert ".welcome" in html


def test_dark_theme_forces_readable_heading():
    html = _theme_frag("theme_dark")
    assert "h1,h2,h3,.card h1,.title{color:#f1f5f9!important}" in html
    assert "#cbd5e1!important" in html  # الوصف/التسميات الفاتحة


# ─────────────────── C2) احتواء أجزاء «قبل الدخول» ───────────────────

def test_prelogin_fragments_wrapped_for_containment():
    from app.radius.services import hotspot_surfaces as hsf
    from app.radius.services import hotspot_templates as ht
    safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    addons = {"announcements": {"enabled": True,
                                "config": {"title": "إعلانات المسجد",
                                           "body": "درس بعد العشاء"}}}
    html = hsf.render_login_surface("classic", safe, addons, tenant_id=1)
    # الأجزاء مَلفوفة في حاوية واحدة بعَرض كامل (تَخرج من صَفّ الـflex).
    assert 'class="hr-prelogin-extras"' in html
    assert "flex:0 0 100%" in html
    assert "flex-direction:column" in html
    # body يَلتفّ كي تَنزل الحاوية تحت البطاقة.
    assert "flex-wrap:wrap!important" in html
    # المُحتوى المُحرَّر (العنوان) ظاهر داخل الحاوية.
    assert "إعلانات المسجد" in html


def test_no_fragments_no_wrapper_unchanged():
    # بلا إضافات مفعّلة: لا حاوية لفّ للأجزاء. نَفحص عُنصر الحاوية الفعليّ
    # لا مُجرّد السلسلة — فاسم الصنف يَظهر أيضًا ضمن مُحدِّدات رَفع البَصمة
    # (z-index) حتى بلا إضافات.
    from app.radius.services import hotspot_surfaces as hsf
    from app.radius.services import hotspot_templates as ht
    safe = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    html = hsf.render_login_surface("classic", safe, {}, tenant_id=1)
    assert '<div class="hr-prelogin-extras"' not in html


def test_announcements_content_is_editable_field():
    # «إعلانات المسجد» تأتي من حقلَي addon قابلَين للتحرير (title/body) —
    # ليست نصًّا مخبوزًا غير قابل للتعديل.
    from app.radius.services import hotspot_addons as ad
    import app.radius.services.hotspot_addons_content  # noqa: F401
    specs = {s.key: s for s in ad.all_addons()}
    spec = specs.get("announcements")
    assert spec is not None, "إضافة الإعلانات غير مُسجَّلة"
    field_keys = {f.key for f in spec.fields}
    assert "title" in field_keys and "body" in field_keys
