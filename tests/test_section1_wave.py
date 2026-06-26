# -*- coding: utf-8 -*-
"""موجة القسم ① «شبكة عامة» — التصاميم الفاخرة المُفرَدة #3/#4/#5.

#3 «الزجاج الجليدي» (frost_mesh) · #4 «لوحة القياس» (speed_dash) ·
#5 «الموجة الزرقاء» (blue_wave). كلٌّ بهويّة خاصّة، عقود الدخول سليمة،
البَصمة خلفيّة، الشريط آمن، العلامة ديناميكيّة. والقسم ① مكتمل بخمسة.
"""
import re
import pytest

NEW = {
    "frost_mesh": {"name": "الزجاج الجليدي", "markers": ("hr-frost-mesh", "fm-hero", "fm-ring", "fm-pills")},
    "speed_dash": {"name": "لوحة القياس", "markers": ("hr-speed-dash", "sd-hero", "sd-dials", "sd-tiles")},
    "blue_wave":  {"name": "الموجة الزرقاء", "markers": ("hr-blue-wave", "bw-hero", "bw-wave", "bw-chips")},
}


@pytest.mark.parametrize("slug", list(NEW))
def test_registered(slug):
    from app.radius.services import hotspot_templates as ht
    assert slug in ht.TEMPLATES_BY_SLUG
    assert ht.TEMPLATES_BY_SLUG[slug].name_ar == NEW[slug]["name"]


@pytest.mark.parametrize("slug", list(NEW))
def test_bespoke_markers_and_no_crosstalk(slug):
    from app.radius.services import hotspot_templates as ht
    html = ht.render(slug, {"TENANT_NAME": "نت", "MOTIF_ICON": "wifi"}, tenant_id=1)
    for m in NEW[slug]["markers"]:
        assert m in html, f"{slug}: عنصر مفقود {m}"
    # لا تداخل مع تصاميم أخرى في القسم.
    others = {"hr-frost-mesh", "hr-speed-dash", "hr-blue-wave",
              "hr-live-portal", "hr-neon-dark"} - {NEW[slug]["markers"][0]}
    for o in others:
        assert o not in html, f"{slug}: تسرّب أسلوب {o}"
    assert '<div class="network-pulse-card">' not in html
    assert "prefers-reduced-motion" in html


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
    html = ht.render(slug, {"TENANT_NAME": "شبكتي", "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html
    assert "شبكتي" in html
    leaks = [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
             if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
    assert not leaks, f"{slug}: تسرّب متغيّر {leaks}"


def test_section1_complete_five_distinct():
    from app.radius.routes import mt_login_designer as route
    gen = [s for s in route._TEMPLATE_SECTIONS if s[0] == "general"][0]
    assert gen[3] == ("live_portal", "neon_dark", "frost_mesh", "speed_dash", "blue_wave")
    assert len(gen[3]) == len(set(gen[3])) == 5
