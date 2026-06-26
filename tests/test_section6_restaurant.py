# -*- coding: utf-8 -*-
"""القسم ⑥ «مطعم» — 5 تصاميم برسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز).

طبق مُقدَّم / ضيافة مذهّبة / قرمزيّ راقٍ (عشاء) / كاجوال مرح / قائمة QR.
رسمات فكتور بلا روابط خارجيّة (آمنة دون إنترنت — حتى الـQR مرسوم لا مولّد)،
عقود الدخول سليمة، البَصمة خلفيّة، الشريط آمن، العلامة ديناميكيّة. القسم مكتمل.
"""
import re
import pytest

NEW = {
    "plated_dish":    {"name": "خلفية الطبق", "markers": ("hr-plated-dish", "pd-hero", "pd-art", "pd-st1")},
    "gilded_dining":  {"name": "الضيافة المذهّبة", "markers": ("hr-gilded-dining", "gd-hero", "gd-art", "gd-spark")},
    "crimson_dining": {"name": "القرمزي الراقي", "markers": ("hr-crimson-dining", "cr-hero", "cr-art", "cr-flame")},
    "food_buddies":   {"name": "تعاون الطعام", "markers": ("hr-food-buddies", "fb-hero", "fb-art", "fb-burger")},
    "menu_board":     {"name": "قائمة QR", "markers": ("hr-menu-board", "mb-hero", "mb-art", "mb-scan")},
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
    art = html.split('class="%s"' % NEW[slug]["markers"][2], 1)[1][:6500]
    assert art.count("<path") + art.count("<rect") + art.count("<circle") + art.count("<ellipse") >= 12, \
        f"{slug}: الرسمة ليست غنيّة"
    # تمييز: لا تسرّب أسلوب شقيق.
    for o in {x["markers"][0] for x in NEW.values()} - {NEW[slug]["markers"][0]}:
        assert o not in html, f"{slug}: تسرّب {o}"
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
    html = ht.render(slug, {"TENANT_NAME": "مطعمي", "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html and "مطعمي" in html
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"{slug}: تسرّب متغيّر {leaks}"


def test_section6_complete_five_distinct():
    from app.radius.routes import mt_login_designer as route
    r = [s for s in route._TEMPLATE_SECTIONS if s[0] == "restaurant"][0]
    assert r[3] == ("plated_dish", "gilded_dining", "crimson_dining", "food_buddies", "menu_board")
    assert len(r[3]) == len(set(r[3])) == 5
