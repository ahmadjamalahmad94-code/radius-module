# -*- coding: utf-8 -*-
"""توحيد ثيم الصفحات الفرعية مع قالب الدخول النشط.

شكوى المالك: صفحة الدخول تأخذ ستايل القالب (مثلًا «البنّي الفاخر»
espresso بلوحته الداكنة الذهبيّة)، لكن بقيّة صفحات الهوت سبوت
(الحالة/الخروج/التحويل/الخطأ) كانت تظهر بثيم أبيض/أزرق عامّ يصطدم
بالقالب. الإصلاح: كل صفحة فرعيّة ترث «جلد» القالب النشط (كتلة :root
الكاملة: تدرّج الخلفيّة/البطاقة/الخطّ/الحوافّ + لون التمييز) ورسمة
التوقيع المضمَّنة على صفحات التحويل — مدفوعًا بالقالب لا مثبّتًا على
واحد. هذا يؤكّد:
  • template_skin يستخرج :root + رسمة SVG للقالب النشط.
  • المرافقة + صفحة التحويل ترث توكنات القالب (لا الأزرق الافتراضي).
  • رسمة التوقيع تظهر على صفحات «جارٍ تحويلك» مع حركتها.
  • غياب slug = سقوط للثيم العامّ القديم (متوافق رجعيًّا).
  • كل placeholders راوتر $(...) ووظائف الصفحات سليمة (إعادة تنسيق فقط).
"""
from __future__ import annotations

from app.radius.services import hotspot_companion_pages as hcp
from app.radius.services import hotspot_surfaces as sf
from app.radius.services import hotspot_templates as ht

# espresso «البنّي الفاخر» — لوحة داكنة بنّيّة + ذهب + رسمة فنجان.
ESPRESSO = {"TENANT_NAME": "مقهى الإسبريسو", "ACCENT_COLOR": "#C9A24B",
            "BG_COLOR": "#20140D", "MOTIF_ICON": "cafe"}
# قالب فاتح مغاير — لإثبات أنّ التوحيد مدفوعٌ بالقالب لا ثابتًا.
LIGHT = {"TENANT_NAME": "شركة الاتصالات", "ACCENT_COLOR": "#1D4ED8",
         "BG_COLOR": "#F4F7FB"}

# توكنٌ توقيعيّ داكن لـespresso موجود فقط في كتلة :root الخاصّة به
# (لا في الثيم العامّ) — مِجسّ موثوق لـ«ورِث جلد القالب».
_ESPRESSO_DARK_CARD = "#1C120D"


def test_template_skin_extracts_root_and_signature_svg():
    safe = ht.validate_vars(ESPRESSO)
    skin = ht.template_skin("espresso_lux", safe)
    # كتلة :root الكاملة بلوحة القالب (لا الأزرق العامّ)
    assert ":root" in skin["tokens_css"]
    assert "--bg-gradient" in skin["tokens_css"]
    assert _ESPRESSO_DARK_CARD in skin["tokens_css"]   # --card-bg الداكن
    # لون التمييز استُبدل فعلًا ({{ACCENT_COLOR}} → القيمة)
    assert "{{ACCENT_COLOR}}" not in skin["tokens_css"]
    assert "#C9A24B" in skin["tokens_css"]
    # رسمة التوقيع المضمَّنة (فنجان الإسبريسو) — SVG حقيقيّ لا أيقونة
    assert skin["svg"].lstrip().startswith("<svg")
    assert "viewBox" in skin["svg"] and len(skin["svg"]) > 300


def test_unknown_slug_returns_empty_skin_failsafe():
    safe = ht.validate_vars(ESPRESSO)
    skin = ht.template_skin("no_such_template", safe)
    assert skin == {"tokens_css": "", "svg": ""}


def test_companions_inherit_active_template_theme():
    pages = hcp.build_all_companions(ESPRESSO, slug="espresso_lux")
    for fn in ("status.html", "logout.html", "alogin.html", "error.html"):
        html = pages[fn]
        # كتلة :root للقالب محقونة → البطاقة الداكنة + التدرّج
        assert _ESPRESSO_DARK_CARD in html, f"{fn} لم يرث جلد القالب"
        assert "--bg-gradient" in html
        # توكنات المرافقة تشير لتوكنات القالب (لا ألوان مثبّتة)
        assert "var(--card-bg" in html and "var(--text-main" in html


def test_companions_without_slug_fall_back_to_generic_theme():
    """غياب slug = الثيم العامّ القديم تمامًا (لا حقن كتلة :root القالب)."""
    pages = hcp.build_all_companions(ESPRESSO)   # بلا slug
    status = pages["status.html"]
    # لم تُحقن كتلة :root الخاصّة بالقالب (لا التوكن الداكن التوقيعيّ)
    assert _ESPRESSO_DARK_CARD not in status
    # لكنها تبقى صفحة حالة صالحة كاملة الوظيفة
    assert "تسجيل الخروج" in status and "$(uptime)" in status


def test_theme_is_template_driven_not_hardcoded():
    """قالبان مختلفان ⇒ ثيمان مختلفان (داكن espresso مقابل الفاتح)."""
    dark = hcp.build_all_companions(ESPRESSO, slug="espresso_lux")["status.html"]
    light = hcp.build_all_companions(LIGHT, slug="corporate_white")["status.html"]
    assert _ESPRESSO_DARK_CARD in dark
    assert _ESPRESSO_DARK_CARD not in light          # ليس مثبّتًا على espresso
    assert "#1D4ED8" in light                          # لون القالب الفاتح


def test_transition_pages_show_signature_illustration_with_motion():
    """صفحات «جارٍ تحويلك» (alogin/radvert + صفحة التحويل) تعرض رسمة
    التوقيع مع حركتها — «يضل الـsvg وتحته جارٍ تحويلك»."""
    pages = hcp.build_all_companions(ESPRESSO, slug="espresso_lux")
    skin = ht.template_skin("espresso_lux", ht.validate_vars(ESPRESSO))
    sig_head = skin["svg"][:40]
    for fn in ("alogin.html", "radvert.html"):
        html = pages[fn]
        assert 'class="hr-illus"' in html               # غلاف الرسمة
        assert sig_head in html                          # رسمة القالب نفسها
        assert "hrfloat" in html                         # حركة الطفو (الروح)
    # صفحة التحويل المستضافة (build_redirect_page)
    red = sf.build_redirect_page(ESPRESSO, slug="espresso_lux")
    assert 'class="hr-illus"' in red and sig_head in red
    assert "جارٍ تحويلك" in red and "hrfloat" in red


def test_redirect_page_inherits_template_palette_and_keeps_function():
    red = sf.build_redirect_page(ESPRESSO, slug="espresso_lux")
    # ورِث لوحة القالب
    assert _ESPRESSO_DARK_CARD in red and "var(--card-bg" in red
    # وظيفة التحويل سليمة (meta + JS + رابط يدويّ)
    assert 'http-equiv="refresh"' in red and "url=status.html" in red
    assert "location.href='status.html'" in red
    assert 'href="status.html"' in red
    # وبلا slug تبقى صفحة صالحة (سقوط آمن)
    plain = sf.build_redirect_page(ESPRESSO)
    assert "تم الاتصال بالإنترنت" in plain
    assert _ESPRESSO_DARK_CARD not in plain


def test_mikrotik_placeholders_preserved_after_restyle():
    """إعادة التنسيق لا تمسّ عقد بروتوكول ميكروتك $(...)."""
    pages = hcp.build_all_companions(ESPRESSO, slug="espresso_lux")
    status = pages["status.html"]
    assert "$(uptime)" in status and "$(username)" in status
    assert "$(ip)" in status and "$(mac)" in status
    assert 'action="$(link-logout)"' in status
    assert "$(link-orig)" in pages["alogin.html"]
    assert "$(error)" in pages["error.html"]
    assert "$(link-login)" in pages["logout.html"]
    # صفحات إعادة التوجيه القياسية تبقى عقودًا حرفيّة
    assert "$(link-redirect)" in pages["rlogin.html"]


def test_fallback_illustration_for_templates_without_signature_svg():
    """قالب بلا رسمة توقيع في الجسم ⇒ رسمة اتّصال احتياطيّة متلوّنة
    بثيمه (لا فراغ، ولا أيقونة مفردة)."""
    safe = ht.validate_vars(LIGHT)
    skin = ht.template_skin("live_portal", safe)   # قالب بلا <svg> جسم
    pages = hcp.build_all_companions(LIGHT, slug="live_portal")
    alogin = pages["alogin.html"]
    assert 'class="hr-illus"' in alogin
    if not skin["svg"]:
        # الرسمة الاحتياطيّة تستعمل لون التمييز عبر var(--accent)
        assert 'stroke="var(--accent)"' in alogin
