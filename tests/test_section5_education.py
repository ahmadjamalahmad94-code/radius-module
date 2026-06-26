# -*- coding: utf-8 -*-
"""موجة القسم ⑤ «مؤسسة تعليمية» — أوّل 3 تصاميم برسمات SVG مُضمَّنة كبطل.

#1 «الحرم الجامعي» (campus) · #2 «المدرسة المرحة» (happy_school) ·
#3 «المكتبة الهادئة» (quiet_library). رسمات فكتور بلا روابط خارجيّة (آمنة دون
إنترنت)، عقود الدخول سليمة، البَصمة خلفيّة، الشريط آمن، العلامة ديناميكيّة.
"""
import re
import pytest

NEW = {
    "campus":        {"name": "الحرم الجامعي",
                      "markers": ("hr-campus", "cm-hero", "cm-art", "cm-flag")},
    "happy_school":  {"name": "المدرسة المرحة",
                      "markers": ("hr-happy-school", "hs-hero", "hs-art", "hs-owl")},
    "quiet_library": {"name": "المكتبة الهادئة",
                      "markers": ("hr-quiet-library", "ql-hero", "ql-art", "ql-glow")},
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
    art = html.split('class="%s"' % NEW[slug]["markers"][2], 1)[1][:5500]
    assert art.count("<path") + art.count("<rect") + art.count("<circle") >= 12, \
        f"{slug}: الرسمة ليست غنيّة"
    assert '<div class="network-pulse-card">' not in html


@pytest.mark.parametrize("slug", list(NEW))
def test_offline_safe_no_external_refs(slug):
    from app.radius.services import hotspot_templates as ht
    html = ht.render(slug, {"MOTIF_ICON": "wifi"}, tenant_id=1)
    assert not re.search(r'<img[^>]+src=["\']https?://', html)
    assert not re.search(r'url\(\s*["\']?https?://', html)
    assert not re.search(r'(?:xlink:)?href=["\']https?://', html)
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
    html = ht.render(slug, {"TENANT_NAME": "جامعتي", "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html
    assert "جامعتي" in html
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"{slug}: تسرّب متغيّر {leaks}"


def test_section5_leads_with_illustrated():
    from app.radius.routes import mt_login_designer as route
    edu = [s for s in route._TEMPLATE_SECTIONS if s[0] == "education"][0]
    assert edu[3][:3] == ("campus", "happy_school", "quiet_library")
    assert len(edu[3]) == len(set(edu[3]))
