# -*- coding: utf-8 -*-
"""قالب «البوابة الأكاديمية» (academic_gate) — يُكمل القسم ⑤ (4 تصاميم)."""
import re


def test_registered_and_completes_section5():
    from app.radius.services import hotspot_templates as ht
    from app.radius.routes import mt_login_designer as route
    assert ht.TEMPLATES_BY_SLUG["academic_gate"].name_ar == "البوابة الأكاديمية"
    edu = [s for s in route._TEMPLATE_SECTIONS if s[0] == "education"][0]
    assert edu[3] == ("campus", "happy_school", "quiet_library", "academic_gate")
    assert len(edu[3]) == len(set(edu[3])) == 4


def test_illustration_and_blocks():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("academic_gate", {"TENANT_NAME": "نت", "MOTIF_ICON": "wifi"}, tenant_id=1)
    for m in ("hr-academic-gate", "ag-hero", "ag-art", "ag-mortar", "ag-blocks"):
        assert m in html, m
    art = html.split('class="ag-art"', 1)[1][:5000]
    assert art.count("<path") + art.count("<rect") + art.count("<circle") >= 12
    assert '<div class="network-pulse-card">' not in html


def test_offline_safe_contracts_brand():
    from app.radius.services import hotspot_templates as ht
    html = ht.render("academic_gate", {"TENANT_NAME": "جامعتي", "MOTIF_ICON": "wifi"}, tenant_id=1)
    assert not re.search(r'<img[^>]+src=["\']https?://', html)
    assert not re.search(r'url\(\s*["\']?https?://', html)
    assert not re.search(r'(?:xlink:)?href=["\']https?://', html)
    for ph in ("$(link-login-only)", "$(chap-id)", "</body>"):
        assert ph in html
    assert 'name="username"' in html and 'class="bottom-nav"' in html
    assert ".hr-vm-pat{position:fixed;inset:0;z-index:-1" in html
    assert "hr-bottombar-safety" in html and "prefers-reduced-motion" in html
    assert "جامعتي" in html
    assert not [l for l in re.findall(r'\{\{[A-Z_]+\}\}', html)
                if l not in ("{{DISTRIBUTORS_HTML}}", "{{OFFERS_HTML}}")]
