# -*- coding: utf-8 -*-
"""موجة القسم ③ «مساحة عمل حر» — تصاميم برسمات SVG مُضمَّنة كبطل.

#1 «المكتب النظيف» (clean_desk) · #2 «الزجاج الأزرق» (blue_glass). كلٌّ يَحمل
رسمة فكتور مُضمَّنة بلا روابط خارجيّة (آمنة دون إنترنت قبل الدخول). عقود الدخول
سليمة، البَصمة خلفيّة، الشريط آمن، العلامة ديناميكيّة، والقسم ③ يَتصدّره الاثنان.
"""
import re
import pytest

NEW = {
    "clean_desk": {"name": "المكتب النظيف",
                   "markers": ("hr-clean-desk", "cd-hero", "cd-art", "cd-st1")},
    "blue_glass": {"name": "الزجاج الأزرق",
                   "markers": ("hr-blue-glass", "bg-hero", "bg-art", "bg-lights")},
}


@pytest.mark.parametrize("slug", list(NEW))
def test_registered(slug):
    from app.radius.services import hotspot_templates as ht
    assert slug in ht.TEMPLATES_BY_SLUG
    assert ht.TEMPLATES_BY_SLUG[slug].name_ar == NEW[slug]["name"]


@pytest.mark.parametrize("slug", list(NEW))
def test_signature_illustration_present(slug):
    from app.radius.services import hotspot_templates as ht
    html = ht.render(slug, {"TENANT_NAME": "نت", "MOTIF_ICON": "wifi"}, tenant_id=1)
    for m in NEW[slug]["markers"]:
        assert m in html, f"{slug}: عنصر الرسمة مفقود {m}"
    # رسمة فكتور حقيقيّة (عدّة عناصر)، لا مجرّد أيقونة — نَفصل عند عُنصر الـSVG
    # نفسه (class="..-art") لا قاعدة الـCSS التي تَسبقه.
    art = html.split('class="%s"' % NEW[slug]["markers"][2], 1)[1][:4500]
    assert art.count("<path") + art.count("<rect") >= 8, f"{slug}: الرسمة ليست غنيّة"
    # لا تداخل أسلوب مع شقيقه.
    other = "hr-blue-glass" if slug == "clean_desk" else "hr-clean-desk"
    assert other not in html
    assert '<div class="network-pulse-card">' not in html


@pytest.mark.parametrize("slug", list(NEW))
def test_offline_safe_no_external_refs(slug):
    # جوهريّ: البوابة الأسيرة بلا إنترنت قبل الدخول — لا روابط صور خارجيّة.
    from app.radius.services import hotspot_templates as ht
    html = ht.render(slug, {"MOTIF_ICON": "wifi"}, tenant_id=1)
    assert not re.search(r'<img[^>]+src=["\']https?://', html), "صورة خارجيّة!"
    assert not re.search(r'url\(\s*["\']?https?://', html), "خلفيّة خارجيّة!"
    assert not re.search(r'(?:xlink:)?href=["\']https?://', html), "مرجع SVG خارجيّ!"
    assert "<svg" in html and "prefers-reduced-motion" in html


@pytest.mark.parametrize("slug", list(NEW))
def test_login_contracts(slug):
    from app.radius.services import hotspot_templates as ht
    html = ht.render(slug, {}, tenant_id=1)
    for ph in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)", "</body>"):
        assert ph in html
    assert 'name="username"' in html and 'name="password"' in html
    assert 'class="bottom-nav"' in html and "mobile-container" in html


@pytest.mark.parametrize("slug", list(NEW))
def test_watermark_bar_dynamic_brand(slug):
    from app.radius.services import hotspot_templates as ht
    html = ht.render(slug, {"TENANT_NAME": "مكتبي", "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html
    assert "مكتبي" in html
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"{slug}: تسرّب متغيّر {leaks}"


def test_section3_leads_with_illustrated():
    from app.radius.routes import mt_login_designer as route
    cw = [s for s in route._TEMPLATE_SECTIONS if s[0] == "cowork"][0]
    assert cw[3][0] == "clean_desk" and cw[3][1] == "blue_glass"
    assert len(cw[3]) == len(set(cw[3]))
