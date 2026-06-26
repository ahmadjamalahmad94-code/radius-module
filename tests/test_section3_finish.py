# -*- coding: utf-8 -*-
"""إكمال القسم ③ «مساحة عمل حر» — تصميمان آخران برسمات SVG مُضمَّنة كبطل.

#3 «الشبكة الرقمية» (dev_grid، محرّر شيفرة) · #4 «البطاقة المضيئة» (glow_card،
استوديو مُضاء). رسمات فكتور بلا روابط خارجيّة (آمنة دون إنترنت)، عقود الدخول
سليمة، البَصمة خلفيّة، الشريط آمن، العلامة ديناميكيّة. والقسم ③ مكتمل بأربعة.
"""
import re
import pytest

NEW = {
    "dev_grid":  {"name": "الشبكة الرقمية",
                  "markers": ("hr-dev-grid", "dv-hero", "dv-art", "dv-caret")},
    "glow_card": {"name": "البطاقة المضيئة",
                  "markers": ("hr-glow-card", "gl-hero", "gl-art", "gl-aura")},
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
    art = html.split('class="%s"' % NEW[slug]["markers"][2], 1)[1][:5000]
    assert art.count("<path") + art.count("<rect") + art.count("<circle") >= 10, \
        f"{slug}: الرسمة ليست غنيّة"
    other = "hr-glow-card" if slug == "dev_grid" else "hr-dev-grid"
    assert other not in html
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
    html = ht.render(slug, {"TENANT_NAME": "وركسبيس", "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html
    assert "وركسبيس" in html
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"{slug}: تسرّب متغيّر {leaks}"


def test_section3_complete_four_distinct():
    from app.radius.routes import mt_login_designer as route
    cw = [s for s in route._TEMPLATE_SECTIONS if s[0] == "cowork"][0]
    assert cw[3] == ("clean_desk", "blue_glass", "dev_grid", "glow_card")
    assert len(cw[3]) == len(set(cw[3])) == 4
